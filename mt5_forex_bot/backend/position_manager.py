"""Live position management.

Runs on every engine tick and looks after positions the bot opened:

* **break-even** - move the stop to entry (plus an offset) once the trade is
  ``break_even_trigger_r`` R in profit,
* **trailing stop** - once ``trailing_start_r`` R in profit, drag the stop
  behind price at ``trailing_distance_atr`` x ATR, never backwards,
* **partial close** - optionally bank part of the position at a configured R,
* **maximum holding time** - close a position that has been open too long,
* **close detection** - positions closed by the broker (SL/TP hit) are matched
  against the terminal's deal history and written back into the local trade log.

Every feature is individually switchable from the Settings page.  Failures are
per-position: one problem ticket never stops the others from being managed.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.config import BotConfig
from backend.database import Database, get_db
from backend.logger import get_logger
from backend.market_data import MarketData, get_market_data
from backend.mt5_connection import MT5Connection, get_connection, load_mt5
from backend.mt5_errors import MT5Error
from backend.notifications import Notifier, get_notifier
from backend.order_manager import OrderManager

log = get_logger("positions")


class PositionManager:
    """Applies the configured position-management rules to open trades."""

    def __init__(
        self,
        config: BotConfig,
        order_manager: OrderManager,
        connection: MT5Connection | None = None,
        market_data: MarketData | None = None,
        database: Database | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.config = config
        self.orders = order_manager
        self.conn = connection or get_connection()
        self.market = market_data or get_market_data()
        self.db = database or get_db()
        self.notifier = notifier or get_notifier()
        self._lock = threading.RLock()
        self._known_tickets: set[int] = set()

    # ------------------------------------------------------------------
    # reading positions
    # ------------------------------------------------------------------
    def list_positions(self, only_bot: bool = False) -> list[dict[str, Any]]:
        """Open positions from the terminal, enriched for the dashboard."""
        try:
            positions = self.conn.positions_get()
        except MT5Error as exc:
            log.debug(f"positions_get failed: {exc.message}")
            return []

        mt5 = load_mt5()
        rows: list[dict[str, Any]] = []
        for position in positions:
            magic = int(getattr(position, "magic", 0))
            if only_bot and magic != int(self.config.magic_number):
                continue
            broker_symbol = str(position.symbol)
            canonical = self.market.to_canonical(broker_symbol)
            direction = "BUY" if int(position.type) == mt5.POSITION_TYPE_BUY else "SELL"
            opened_at = datetime.fromtimestamp(int(position.time), tz=timezone.utc)
            stored = self.db.get_trade(int(position.ticket)) or {}

            row = {
                "ticket": int(position.ticket),
                "symbol": canonical,
                "broker_symbol": broker_symbol,
                "direction": direction,
                "volume": float(position.volume),
                "entry_price": float(position.price_open),
                "current_price": float(position.price_current),
                "stop_loss": float(position.sl) or None,
                "take_profit": float(position.tp) or None,
                "initial_sl": stored.get("initial_sl"),
                "profit": float(position.profit),
                "swap": float(getattr(position, "swap", 0.0)),
                "commission": float(getattr(position, "commission", 0.0) or 0.0),
                "opened_at": opened_at.isoformat(),
                "age_hours": round((datetime.now(timezone.utc) - opened_at).total_seconds() / 3600, 2),
                "magic": magic,
                "is_bot_position": magic == int(self.config.magic_number),
                "strategy": stored.get("strategy"),
                "comment": str(getattr(position, "comment", "")),
            }
            row["r_multiple"] = self._r_multiple(row)
            rows.append(row)

        self._persist(rows)
        return rows

    def _persist(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            existing = self.db.get_position(row["ticket"]) or {}
            self.db.upsert_position(
                {
                    **row,
                    "initial_sl": row.get("initial_sl") or existing.get("initial_sl") or row.get("stop_loss"),
                    "break_even_done": existing.get("break_even_done", 0),
                    "partial_done": existing.get("partial_done", 0),
                }
            )
        self.db.delete_positions_except([row["ticket"] for row in rows])

    # ------------------------------------------------------------------
    @staticmethod
    def _r_multiple(position: dict[str, Any]) -> float | None:
        """How many multiples of the original risk the trade is up."""
        entry = position.get("entry_price")
        initial_sl = position.get("initial_sl") or position.get("stop_loss")
        current = position.get("current_price")
        if not entry or not initial_sl or not current:
            return None
        risk = abs(float(entry) - float(initial_sl))
        if risk <= 0:
            return None
        move = float(current) - float(entry)
        if position["direction"] == "SELL":
            move = -move
        return round(move / risk, 3)

    # ------------------------------------------------------------------
    # management loop
    # ------------------------------------------------------------------
    def manage(self) -> list[dict[str, Any]]:
        """Apply every enabled rule to every bot position. Returns the actions."""
        actions: list[dict[str, Any]] = []
        with self._lock:
            positions = self.list_positions(only_bot=True)
            for position in positions:
                try:
                    actions.extend(self._manage_one(position))
                except MT5Error as exc:
                    log.error(
                        f"Position management failed for #{position['ticket']}: {exc.message}",
                        symbol=position["symbol"],
                    )
                except Exception as exc:  # a bug here must not stop the engine
                    log.error(
                        f"Unexpected error managing #{position['ticket']}: {exc}",
                        symbol=position["symbol"], exc_info=True,
                    )
            self.detect_closed_positions({p["ticket"] for p in positions})
        return actions

    def _manage_one(self, position: dict[str, Any]) -> list[dict[str, Any]]:
        cfg = self.config
        actions: list[dict[str, Any]] = []
        ticket = position["ticket"]
        stored = self.db.get_position(ticket) or {}
        r_multiple = position.get("r_multiple")

        # --- maximum holding time -------------------------------------
        if cfg.max_holding_hours > 0 and position["age_hours"] >= cfg.max_holding_hours:
            self.orders.close_position(ticket, reason="max_holding_time")
            log.info(
                f"Closed #{ticket} after {position['age_hours']:.1f}h "
                f"(max holding time {cfg.max_holding_hours}h)",
                symbol=position["symbol"],
            )
            return [{"ticket": ticket, "action": "max_holding_time_close"}]

        if r_multiple is None:
            return actions

        # --- partial close --------------------------------------------
        if (
            cfg.partial_close_enabled
            and not stored.get("partial_done")
            and r_multiple >= cfg.partial_close_at_r
        ):
            spec = self.market.symbol_spec(position["broker_symbol"])
            part = spec.normalize_volume(position["volume"] * cfg.partial_close_percent / 100.0)
            remaining = position["volume"] - part
            if part >= spec.volume_min and remaining >= spec.volume_min:
                self.orders.close_position(ticket, volume=part, reason="partial_take_profit")
                self.db.set_position_flag(ticket, "partial_done", True)
                log.trade(
                    f"Partial close #{ticket}: {part} lots banked at {r_multiple:.2f}R",
                    symbol=position["symbol"],
                )
                actions.append({"ticket": ticket, "action": "partial_close", "volume": part})
                position = {**position, "volume": remaining}

        # --- break-even ------------------------------------------------
        if (
            cfg.break_even_enabled
            and not stored.get("break_even_done")
            and r_multiple >= cfg.break_even_trigger_r
        ):
            spec = self.market.symbol_spec(position["broker_symbol"])
            offset = cfg.break_even_offset_points * spec.point
            entry = float(position["entry_price"])
            new_sl = entry + offset if position["direction"] == "BUY" else entry - offset
            if self._improves_stop(position, new_sl, spec.point):
                self.orders.modify_position(ticket, stop_loss=new_sl, take_profit=position.get("take_profit"))
                self.db.set_position_flag(ticket, "break_even_done", True)
                log.trade(
                    f"Break-even armed on #{ticket}: SL moved to {spec.normalize_price(new_sl)} "
                    f"at {r_multiple:.2f}R",
                    symbol=position["symbol"],
                )
                actions.append({"ticket": ticket, "action": "break_even", "stop_loss": new_sl})
                position = {**position, "stop_loss": new_sl}

        # --- trailing stop --------------------------------------------
        if cfg.trailing_stop_enabled and r_multiple >= cfg.trailing_start_r:
            new_sl = self._trailing_stop_price(position)
            if new_sl is not None:
                spec = self.market.symbol_spec(position["broker_symbol"])
                if self._improves_stop(position, new_sl, spec.point):
                    self.orders.modify_position(
                        ticket, stop_loss=new_sl, take_profit=position.get("take_profit")
                    )
                    log.trade(
                        f"Trailing stop on #{ticket}: SL -> {spec.normalize_price(new_sl)} "
                        f"({r_multiple:.2f}R)",
                        symbol=position["symbol"],
                    )
                    actions.append({"ticket": ticket, "action": "trailing_stop", "stop_loss": new_sl})
        return actions

    @staticmethod
    def _improves_stop(position: dict[str, Any], new_sl: float, point: float) -> bool:
        """Only ever move a stop towards profit, and only by a real amount."""
        current = position.get("stop_loss")
        price = float(position["current_price"])
        if position["direction"] == "BUY":
            if new_sl >= price - point:       # never above current price
                return False
            return current is None or new_sl > float(current) + point / 2
        if new_sl <= price + point:
            return False
        return current is None or new_sl < float(current) - point / 2

    def _trailing_stop_price(self, position: dict[str, Any]) -> float | None:
        """ATR based trailing distance behind the current price."""
        try:
            frame = self.market.get_rates(
                position["broker_symbol"], self.config.timeframe, self.config.atr_period * 4 + 20
            )
        except MT5Error:
            return None
        if frame.empty:
            return None

        from backend.indicators import atr as atr_indicator

        atr_series = atr_indicator(frame["high"], frame["low"], frame["close"], self.config.atr_period)
        atr_value = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
        if atr_value <= 0:
            return None

        distance = atr_value * self.config.trailing_distance_atr
        price = float(position["current_price"])
        spec = self.market.symbol_spec(position["broker_symbol"])
        minimum = spec.stops_level_points * spec.point + spec.point
        distance = max(distance, minimum)
        return spec.normalize_price(
            price - distance if position["direction"] == "BUY" else price + distance
        )

    # ------------------------------------------------------------------
    # closures made by the broker (SL / TP hits)
    # ------------------------------------------------------------------
    def detect_closed_positions(self, live_tickets: set[int]) -> list[dict[str, Any]]:
        """Reconcile the local trade log with the terminal after SL/TP hits."""
        closed: list[dict[str, Any]] = []
        for trade in self.db.open_trades():
            ticket = int(trade["ticket"] or 0)
            if not ticket or ticket in live_tickets:
                continue
            detail = self._closing_deal(ticket)
            profit = detail.get("profit", 0.0)
            exit_price = detail.get("price")
            reason = self._close_reason(trade, exit_price, detail.get("comment", ""))
            self.db.close_trade(
                ticket, exit_price, profit, reason,
                commission=detail.get("commission", 0.0), swap=detail.get("swap", 0.0),
                closed_at=detail.get("time"),
            )
            log.trade(
                f"Position #{ticket} {trade['symbol']} closed by the broker "
                f"({reason}) P/L {profit:+.2f}",
                symbol=trade["symbol"],
            )
            self.notifier.trade_closed(
                trade["symbol"], trade["direction"], float(trade["volume"] or 0), profit, reason, ticket
            )
            closed.append({"ticket": ticket, "reason": reason, "profit": profit})
        return closed

    def _closing_deal(self, ticket: int) -> dict[str, Any]:
        """Find the deal that closed a position in the terminal's history."""
        try:
            deals = self.conn.history_deals_get(
                datetime.now(timezone.utc) - timedelta(days=7),
                datetime.now(timezone.utc) + timedelta(minutes=5),
                position=ticket,
            )
        except MT5Error as exc:
            log.debug(f"history_deals_get failed for #{ticket}: {exc.message}")
            return {}

        closing = [d for d in deals if int(getattr(d, "entry", 0)) in (1, 2)]
        if not closing:
            return {}
        profit = sum(
            float(getattr(d, "profit", 0.0)) + float(getattr(d, "commission", 0.0))
            + float(getattr(d, "swap", 0.0))
            for d in closing
        )
        last = closing[-1]
        return {
            "profit": profit,
            "price": float(getattr(last, "price", 0.0)) or None,
            "commission": sum(float(getattr(d, "commission", 0.0)) for d in closing),
            "swap": sum(float(getattr(d, "swap", 0.0)) for d in closing),
            "comment": str(getattr(last, "comment", "")),
            "time": datetime.fromtimestamp(int(getattr(last, "time", 0)), tz=timezone.utc).isoformat(),
        }

    @staticmethod
    def _close_reason(trade: dict[str, Any], exit_price: float | None, comment: str) -> str:
        """Classify a broker-side close as SL, TP or something else."""
        lowered = (comment or "").lower()
        if "sl" in lowered:
            return "SL"
        if "tp" in lowered:
            return "TP"
        if exit_price is None:
            return "closed"
        stop_loss = trade.get("stop_loss")
        take_profit = trade.get("take_profit")
        distances: list[tuple[float, str]] = []
        if stop_loss:
            distances.append((abs(exit_price - float(stop_loss)), "SL"))
        if take_profit:
            distances.append((abs(exit_price - float(take_profit)), "TP"))
        if not distances:
            return "closed"
        return min(distances)[1]

    # ------------------------------------------------------------------
    def close_all(self, only_bot: bool = False, reason: str = "close_all") -> dict[str, Any]:
        return self.orders.close_all(only_bot_positions=only_bot, reason=reason)
