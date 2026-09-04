"""Risk management - the layer that decides *whether* and *how big* to trade.

Rules enforced here (all of them, on every single order):

* risk per trade is a fixed percentage of the **current account balance**,
* the stop loss distance comes from ATR, never from a fixed pip count,
* the take profit must satisfy the configured minimum risk/reward,
* lot size is derived from balance, risk and stop distance, then snapped to the
  broker's own volume step and clamped by both the broker's and the user's
  maximum,
* a hard daily-loss limit stops new trades for the rest of the day,
* a hard cap on simultaneous open trades,
* spread and free-margin sanity checks before anything is sent.

Explicitly **not** implemented, by design: martingale, grid/averaging down, or
any lot size that depends on the outcome of previous trades.  Position size is
a function of balance, risk % and stop distance - nothing else.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timezone
from typing import Any

from backend.config import BotConfig
from backend.database import Database, get_db, today_key
from backend.logger import get_logger
from backend.market_data import MarketData, SymbolSpec, get_market_data
from backend.mt5_connection import MT5Connection, get_connection
from backend.mt5_errors import MT5Error

log = get_logger("risk")


# ----------------------------------------------------------------------
# pure calculations (no MT5, fully unit-testable)
# ----------------------------------------------------------------------
def atr_stop_distance(atr_value: float, multiplier: float) -> float:
    """Stop distance in price units."""
    if atr_value is None or atr_value <= 0:
        raise ValueError("ATR must be positive to derive a stop distance")
    return float(atr_value) * float(multiplier)


def apply_min_stop_distance(distance: float, spec: SymbolSpec) -> float:
    """Widen a stop that would sit inside the broker's minimum stop level."""
    minimum = spec.stops_level_points * spec.point
    # Always keep at least one point, plus the broker's own freeze/stops level.
    return max(distance, minimum + spec.point, spec.point)


def stop_loss_price(entry: float, direction: str, distance: float, spec: SymbolSpec) -> float:
    """SL price for a BUY (below entry) or SELL (above entry)."""
    if direction.upper() == "BUY":
        return spec.normalize_price(entry - distance)
    return spec.normalize_price(entry + distance)


def take_profit_price(
    entry: float, direction: str, distance: float, risk_reward: float, spec: SymbolSpec
) -> float:
    """TP placed ``risk_reward`` times the stop distance away from entry."""
    target = distance * float(risk_reward)
    if direction.upper() == "BUY":
        return spec.normalize_price(entry + target)
    return spec.normalize_price(entry - target)


def risk_reward_ratio(entry: float, stop_loss: float, take_profit: float) -> float:
    """Realised R:R of a concrete entry/SL/TP triplet."""
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    if risk <= 0:
        return 0.0
    return reward / risk


def loss_per_lot(spec: SymbolSpec, stop_distance: float) -> float:
    """Money lost on one lot if price moves ``stop_distance`` against us.

    Uses the broker's tick value when it is published (it already accounts for
    currency conversion), otherwise falls back to contract size, which is exact
    for pairs quoted in the account currency and a close approximation
    elsewhere.
    """
    if stop_distance <= 0:
        raise ValueError("Stop distance must be positive")
    if spec.tick_value > 0 and spec.tick_size > 0:
        ticks = stop_distance / spec.tick_size
        return ticks * spec.tick_value
    return stop_distance * spec.contract_size


def calculate_lot_size(
    balance: float,
    risk_percent: float,
    stop_distance: float,
    spec: SymbolSpec,
    max_lot: float,
    min_lot: float | None = None,
    money_per_lot: float | None = None,
) -> dict[str, Any]:
    """Position size from balance, risk % and stop distance.

    Returns a dict with the raw and the broker-normalised volume, the money at
    risk and - importantly - ``within_risk``: ``False`` means even the smallest
    tradable volume would risk more than the configured percentage, and the
    caller must refuse the trade rather than quietly over-risking.
    """
    if balance <= 0:
        raise ValueError("Account balance must be positive")
    risk_percent = max(0.0, float(risk_percent))
    risk_amount = balance * risk_percent / 100.0

    per_lot = money_per_lot if money_per_lot and money_per_lot > 0 else loss_per_lot(spec, stop_distance)
    if per_lot <= 0:
        raise ValueError("Could not determine the money at risk per lot for this symbol")

    raw_volume = risk_amount / per_lot

    floor_lot = max(spec.volume_min, float(min_lot) if min_lot else 0.0)
    ceiling_lot = min(spec.volume_max, float(max_lot))
    if ceiling_lot < floor_lot:
        ceiling_lot = floor_lot

    # Round *down* onto the volume step so we never exceed the risk budget.
    step = spec.volume_step or 0.01
    stepped = math.floor(raw_volume / step) * step
    stepped = round(stepped, 8)
    volume = min(max(stepped, floor_lot), ceiling_lot)
    volume = spec.normalize_volume(volume)

    actual_risk = volume * per_lot
    return {
        "volume": volume,
        "raw_volume": round(raw_volume, 4),
        "risk_amount": round(risk_amount, 2),
        "actual_risk_amount": round(actual_risk, 2),
        "actual_risk_percent": round(actual_risk / balance * 100, 3) if balance else 0.0,
        "money_per_lot": round(per_lot, 4),
        "min_lot": floor_lot,
        "max_lot": ceiling_lot,
        "capped_by_max_lot": stepped > ceiling_lot,
        # A tiny tolerance absorbs float noise on exact-fit lot sizes. When the
        # broker's minimum volume alone would blow the risk budget this stays
        # False and the trade is refused - it is never rounded away.
        "within_risk": actual_risk <= risk_amount * 1.0001,
        "below_min_lot": raw_volume < floor_lot,
    }


# ----------------------------------------------------------------------
# decisions
# ----------------------------------------------------------------------
@dataclass
class RiskDecision:
    """Answer to 'may I open this trade, and with what size?'"""

    allowed: bool
    reason: str = ""
    volume: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    stop_distance: float | None = None
    risk_amount: float = 0.0
    risk_percent: float = 0.0
    risk_reward: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "volume": self.volume,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "stop_distance": self.stop_distance,
            "risk_amount": self.risk_amount,
            "risk_percent": self.risk_percent,
            "risk_reward": self.risk_reward,
            "details": self.details,
        }


@dataclass
class DailyStatus:
    """Where the account stands against the daily loss limit."""

    day: str
    starting_balance: float
    balance: float
    equity: float
    realized_pnl: float
    floating_pnl: float
    total_pnl: float
    loss_percent: float
    limit_percent: float
    limit_hit: bool
    trades_today: int
    wins_today: int
    losses_today: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "starting_balance": round(self.starting_balance, 2),
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "floating_pnl": round(self.floating_pnl, 2),
            "total_pnl": round(self.total_pnl, 2),
            "loss_percent": round(self.loss_percent, 3),
            "limit_percent": self.limit_percent,
            "limit_hit": self.limit_hit,
            "remaining_percent": round(max(0.0, self.limit_percent - self.loss_percent), 3),
            "trades_today": self.trades_today,
            "wins_today": self.wins_today,
            "losses_today": self.losses_today,
        }


class RiskManager:
    """Applies the risk rules against live account state."""

    def __init__(
        self,
        config: BotConfig,
        database: Database | None = None,
        market_data: MarketData | None = None,
        connection: MT5Connection | None = None,
    ) -> None:
        self.config = config
        self.db = database or get_db()
        self.market = market_data or get_market_data()
        self.conn = connection or get_connection()
        self._lock = threading.RLock()
        self._limit_announced_for: str | None = None

    # ------------------------------------------------------------------
    # daily loss guard
    # ------------------------------------------------------------------
    def _day_start_utc(self) -> datetime:
        return datetime.combine(date.today(), dt_time.min).replace(tzinfo=timezone.utc)

    def realized_pnl_today(self) -> tuple[float, int, int, int]:
        """(pnl, trades, wins, losses) from the terminal's own deal history."""
        start = self._day_start_utc()
        end = datetime.now(timezone.utc)
        try:
            deals = self.conn.history_deals_get(start, end)
        except MT5Error as exc:
            log.debug(f"Falling back to the local trade log for today's P/L: {exc.message}")
            return self._realized_pnl_today_from_db()

        pnl = 0.0
        trades = wins = losses = 0
        for deal in deals:
            entry = int(getattr(deal, "entry", 0))
            deal_type = int(getattr(deal, "type", 0))
            # entry 1 = DEAL_ENTRY_OUT (a closing deal), types 0/1 = buy/sell
            if deal_type > 1:
                continue
            profit = (
                float(getattr(deal, "profit", 0.0))
                + float(getattr(deal, "commission", 0.0))
                + float(getattr(deal, "swap", 0.0))
            )
            pnl += profit
            if entry in (1, 2):  # OUT / INOUT
                trades += 1
                if profit > 0:
                    wins += 1
                elif profit < 0:
                    losses += 1
        return pnl, trades, wins, losses

    def _realized_pnl_today_from_db(self) -> tuple[float, int, int, int]:
        rows = self.db.closed_trades_since(self._day_start_utc().isoformat())
        pnl = sum(
            float(r.get("profit") or 0) + float(r.get("commission") or 0) + float(r.get("swap") or 0)
            for r in rows
        )
        wins = sum(1 for r in rows if float(r.get("profit") or 0) > 0)
        losses = sum(1 for r in rows if float(r.get("profit") or 0) < 0)
        return pnl, len(rows), wins, losses

    def daily_status(self, account: dict[str, Any] | None = None) -> DailyStatus:
        """Current standing against the daily loss limit."""
        with self._lock:
            account = account or self.conn.account_info()
            balance = float(account["balance"])
            equity = float(account["equity"])
            floating = float(account.get("profit", equity - balance))

            realized, trades, wins, losses = self.realized_pnl_today()
            day = today_key()

            # The day's starting balance is captured once and then reused, so a
            # restart mid-day cannot reset the loss budget.
            row = self.db.get_day(day)
            if row is None:
                starting = balance - realized
                self.db.ensure_day(day, starting)
                row = self.db.get_day(day) or {"starting_balance": starting}
            starting = float(row.get("starting_balance") or balance)
            if starting <= 0:
                starting = balance or 1.0

            total = realized + floating
            loss_percent = max(0.0, -total / starting * 100.0)
            limit = float(self.config.max_daily_loss_percent)
            limit_hit = loss_percent >= limit

            self.db.update_day(
                day,
                ending_balance=balance,
                realized_pnl=realized,
                trades=trades,
                wins=wins,
                losses=losses,
                daily_loss_hit=int(limit_hit),
            )

            if limit_hit and self._limit_announced_for != day:
                self._limit_announced_for = day
                log.warning(
                    f"DAILY LOSS LIMIT REACHED: {loss_percent:.2f}% of {starting:.2f} "
                    f"(limit {limit:.2f}%). No new trades will be opened today; "
                    "existing positions stay monitored."
                )
            elif not limit_hit and self._limit_announced_for == day:
                self._limit_announced_for = None

            return DailyStatus(
                day=day,
                starting_balance=starting,
                balance=balance,
                equity=equity,
                realized_pnl=realized,
                floating_pnl=floating,
                total_pnl=total,
                loss_percent=loss_percent,
                limit_percent=limit,
                limit_hit=limit_hit,
                trades_today=trades,
                wins_today=wins,
                losses_today=losses,
            )

    def daily_limit_hit(self, account: dict[str, Any] | None = None) -> bool:
        try:
            return self.daily_status(account).limit_hit
        except MT5Error as exc:
            log.error(f"Could not evaluate the daily loss limit: {exc.message}")
            return True  # fail closed: no new trades when the state is unknown

    # ------------------------------------------------------------------
    # trade admission
    # ------------------------------------------------------------------
    def evaluate_trade(
        self,
        symbol: str,
        broker_symbol: str,
        direction: str,
        entry_price: float,
        atr_value: float,
        open_positions: int,
        account: dict[str, Any] | None = None,
        spec: SymbolSpec | None = None,
    ) -> RiskDecision:
        """Full pre-trade check: limits, stops, size, margin."""
        direction = direction.upper()
        cfg = self.config
        details: dict[str, Any] = {}

        try:
            account = account or self.conn.account_info()
        except MT5Error as exc:
            return RiskDecision(False, f"Account information unavailable: {exc.message}")

        # --- hard stops ------------------------------------------------
        daily = self.daily_status(account)
        details["daily"] = daily.to_dict()
        if daily.limit_hit:
            return RiskDecision(
                False,
                f"Daily loss limit reached ({daily.loss_percent:.2f}% of "
                f"{daily.limit_percent:.2f}%) - no new trades today",
                details=details,
            )

        if open_positions >= cfg.max_open_trades:
            return RiskDecision(
                False,
                f"Maximum simultaneous trades reached ({open_positions}/{cfg.max_open_trades})",
                details=details,
            )

        # --- symbol state ---------------------------------------------
        try:
            spec = spec or self.market.symbol_spec(broker_symbol)
            tick = self.market.get_tick(broker_symbol)
        except MT5Error as exc:
            return RiskDecision(False, exc.message, details=details)

        if not spec.tradable:
            return RiskDecision(False, f"{broker_symbol} is not open for trading right now", details=details)

        if tick["spread_points"] > cfg.max_spread_points:
            return RiskDecision(
                False,
                f"Spread too wide: {tick['spread_points']} points "
                f"(limit {cfg.max_spread_points})",
                details=details,
            )

        entry = float(tick["ask"] if direction == "BUY" else tick["bid"])
        details["entry_price"] = entry
        details["spread_points"] = tick["spread_points"]

        # --- stops -----------------------------------------------------
        try:
            distance = apply_min_stop_distance(
                atr_stop_distance(atr_value, cfg.atr_sl_multiplier), spec
            )
        except ValueError as exc:
            return RiskDecision(False, str(exc), details=details)

        stop = stop_loss_price(entry, direction, distance, spec)
        target = take_profit_price(entry, direction, distance, cfg.min_risk_reward, spec)
        rr = risk_reward_ratio(entry, stop, target)
        if rr < cfg.min_risk_reward - 1e-9:
            return RiskDecision(
                False,
                f"Risk/reward {rr:.2f} is below the minimum {cfg.min_risk_reward:.2f}",
                details=details,
            )

        # --- size ------------------------------------------------------
        money_per_lot = self._money_per_lot(broker_symbol, direction, entry, distance, spec)
        try:
            sizing = calculate_lot_size(
                balance=float(account["balance"]),
                risk_percent=cfg.risk_per_trade_percent,
                stop_distance=distance,
                spec=spec,
                max_lot=cfg.max_lot_size,
                min_lot=cfg.min_lot_size,
                money_per_lot=money_per_lot,
            )
        except ValueError as exc:
            return RiskDecision(False, str(exc), details=details)
        details["sizing"] = sizing

        if sizing["volume"] <= 0:
            return RiskDecision(False, "Calculated lot size is zero", details=details)

        if not sizing["within_risk"]:
            return RiskDecision(
                False,
                (
                    f"The broker's minimum volume ({sizing['min_lot']}) would risk "
                    f"{sizing['actual_risk_percent']:.2f}%, above the configured "
                    f"{cfg.risk_per_trade_percent:.2f}% - trade skipped"
                ),
                details=details,
            )

        # --- margin ----------------------------------------------------
        margin_needed = self._required_margin(broker_symbol, direction, sizing["volume"], entry)
        if margin_needed is not None:
            details["margin_required"] = round(margin_needed, 2)
            free_margin = float(account.get("free_margin", 0.0))
            details["free_margin"] = round(free_margin, 2)
            if margin_needed > free_margin:
                return RiskDecision(
                    False,
                    f"Insufficient margin: {margin_needed:.2f} required, "
                    f"{free_margin:.2f} free",
                    details=details,
                )

        return RiskDecision(
            allowed=True,
            reason="Risk checks passed",
            volume=sizing["volume"],
            stop_loss=stop,
            take_profit=target,
            stop_distance=distance,
            risk_amount=sizing["actual_risk_amount"],
            risk_percent=sizing["actual_risk_percent"],
            risk_reward=round(rr, 2),
            details=details,
        )

    # ------------------------------------------------------------------
    def _money_per_lot(
        self, broker_symbol: str, direction: str, entry: float, distance: float, spec: SymbolSpec
    ) -> float | None:
        """Ask MT5 what one lot loses over the stop distance (exact, converted)."""
        try:
            from backend.mt5_connection import load_mt5

            mt5 = load_mt5()
            order_type = mt5.ORDER_TYPE_BUY if direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL
            exit_price = entry - distance if direction.upper() == "BUY" else entry + distance
            profit = self.conn.order_calc_profit(order_type, broker_symbol, 1.0, entry, exit_price)
            if profit is not None and profit < 0:
                return abs(profit)
        except (MT5Error, AttributeError, ValueError) as exc:
            log.debug(f"order_calc_profit unavailable for {broker_symbol}: {exc}")
        return None

    def _required_margin(
        self, broker_symbol: str, direction: str, volume: float, price: float
    ) -> float | None:
        try:
            from backend.mt5_connection import load_mt5

            mt5 = load_mt5()
            order_type = mt5.ORDER_TYPE_BUY if direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL
            return self.conn.order_calc_margin(order_type, broker_symbol, volume, price)
        except (MT5Error, AttributeError, ValueError) as exc:
            log.debug(f"order_calc_margin unavailable for {broker_symbol}: {exc}")
            return None

    # ------------------------------------------------------------------
    def summary(self, open_positions: int = 0) -> dict[str, Any]:
        """Risk panel payload for the dashboard."""
        cfg = self.config
        payload: dict[str, Any] = {
            "risk_per_trade_percent": cfg.risk_per_trade_percent,
            "max_daily_loss_percent": cfg.max_daily_loss_percent,
            "max_open_trades": cfg.max_open_trades,
            "open_trades": open_positions,
            "min_risk_reward": cfg.min_risk_reward,
            "atr_period": cfg.atr_period,
            "atr_sl_multiplier": cfg.atr_sl_multiplier,
            "max_lot_size": cfg.max_lot_size,
            "max_spread_points": cfg.max_spread_points,
            "daily": None,
            "error": None,
        }
        try:
            payload["daily"] = self.daily_status().to_dict()
        except MT5Error as exc:
            payload["error"] = exc.message
        return payload
