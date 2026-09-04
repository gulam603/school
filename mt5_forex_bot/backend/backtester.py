"""Historical backtesting.

Candles come from the same MT5 terminal the live bot trades on
(``copy_rates_range``), and the simulation reuses the *same* strategy classes
and the *same* risk/lot-size functions as live trading, so a backtest cannot
drift away from what the bot would actually have done.

Modelling choices (deliberately conservative):

* a signal on the close of bar *i* is entered at the **open of bar i+1**,
  never at the signal bar's own close,
* the entry price pays the spread; stops and targets are checked against the
  bar's high/low,
* if a bar's range covers both the stop and the target, the **stop** is assumed
  to have been hit first,
* break-even / trailing adjustments are evaluated on bar closes,
* position size is recomputed from the running balance on every trade with the
  live ``calculate_lot_size``.

Past performance never guarantees future results - the warning travels with
every result payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.config import BotConfig
from backend.database import Database, get_db
from backend.logger import get_logger
from backend.market_data import MarketData, SymbolSpec, get_market_data
from backend.mt5_errors import MT5Error
from backend.risk_manager import (
    apply_min_stop_distance,
    atr_stop_distance,
    calculate_lot_size,
    loss_per_lot,
    stop_loss_price,
    take_profit_price,
)
from strategies import get_strategy

log = get_logger("backtest")

DISCLAIMER = (
    "Backtest results are a simulation over historical data. Historical "
    "performance does not guarantee future results. Real trading additionally "
    "involves slippage, requotes, variable spread, swap and execution delays."
)


@dataclass
class BacktestTrade:
    """One simulated round trip."""

    ticket: int
    direction: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    volume: float
    stop_loss: float
    take_profit: float
    profit: float
    r_multiple: float
    exit_reason: str
    balance_after: float
    bars_held: int
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": round(self.entry_price, 6),
            "exit_time": self.exit_time,
            "exit_price": round(self.exit_price, 6),
            "volume": self.volume,
            "stop_loss": round(self.stop_loss, 6),
            "take_profit": round(self.take_profit, 6),
            "profit": round(self.profit, 2),
            "r_multiple": round(self.r_multiple, 3),
            "exit_reason": self.exit_reason,
            "balance_after": round(self.balance_after, 2),
            "bars_held": self.bars_held,
            "reason": self.reason,
        }


@dataclass
class _OpenTrade:
    direction: str
    entry_index: int
    entry_time: str
    entry_price: float
    volume: float
    stop_loss: float
    take_profit: float
    initial_sl: float
    money_per_lot_unit: float          # money per 1 lot per 1.0 of price movement
    reason: str
    break_even_done: bool = False
    partial_done: bool = False

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_price - self.initial_sl)


class Backtester:
    """Bar-by-bar simulation of a strategy over historical MT5 candles."""

    def __init__(
        self,
        config: BotConfig,
        market_data: MarketData | None = None,
        database: Database | None = None,
    ) -> None:
        self.config = config
        self.market = market_data or get_market_data()
        self.db = database or get_db()

    # ------------------------------------------------------------------
    def run(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        initial_balance: float = 10_000.0,
        risk_percent: float | None = None,
        strategy_name: str | None = None,
        params: dict[str, Any] | None = None,
        save: bool = True,
    ) -> dict[str, Any]:
        """Run a backtest and return the full result payload."""
        symbol = symbol.upper()
        timeframe = timeframe.upper()
        risk_percent = float(risk_percent if risk_percent is not None else self.config.risk_per_trade_percent)
        strategy_name = strategy_name or self.config.strategy

        broker_symbol = self.market.resolve(symbol, self.config.symbol_prefix, self.config.symbol_suffix)
        spec = self.market.symbol_spec(broker_symbol)
        frame = self.market.get_rates_range(broker_symbol, timeframe, start, end)
        if frame.empty:
            raise MT5Error(
                f"No historical candles for {broker_symbol} {timeframe} between "
                f"{start:%Y-%m-%d} and {end:%Y-%m-%d}. Open the symbol's chart in "
                "MetaTrader 5 once so the terminal downloads its history."
            )

        strategy = get_strategy(strategy_name, params or self.config.strategy_params())
        prepared = strategy.prepare(frame)
        warmup = max(strategy.required_bars, self.config.atr_period + 2)
        if len(prepared) <= warmup + 5:
            raise MT5Error(
                f"Only {len(prepared)} candles available; the strategy needs at least "
                f"{warmup + 5}. Widen the date range or pick a lower timeframe."
            )

        log.info(
            f"Backtest {symbol} {timeframe} {start:%Y-%m-%d}..{end:%Y-%m-%d} "
            f"- {len(prepared)} candles, {strategy.display_name}, risk {risk_percent}%"
        )

        result = self._simulate(
            prepared, strategy, spec, symbol, timeframe, initial_balance, risk_percent, warmup
        )
        result.update(
            {
                "symbol": symbol,
                "broker_symbol": broker_symbol,
                "timeframe": timeframe,
                "strategy": strategy.name,
                "strategy_display_name": strategy.display_name,
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
                "initial_balance": round(initial_balance, 2),
                "risk_percent": risk_percent,
                "candles": int(len(prepared)),
                "params": strategy.params,
                "disclaimer": DISCLAIMER,
            }
        )
        if save:
            result["id"] = self.db.insert_backtest(result)
        return result

    # ------------------------------------------------------------------
    def _simulate(
        self,
        data: pd.DataFrame,
        strategy: Any,
        spec: SymbolSpec,
        symbol: str,
        timeframe: str,
        initial_balance: float,
        risk_percent: float,
        warmup: int,
    ) -> dict[str, Any]:
        cfg = self.config
        balance = float(initial_balance)
        equity_curve: list[dict[str, Any]] = []
        trades: list[BacktestTrade] = []
        open_trade: _OpenTrade | None = None
        pending: dict[str, Any] | None = None
        ticket = 1
        spread = spec.spread_points * spec.point
        peak_balance = balance
        max_drawdown_money = 0.0
        max_drawdown_percent = 0.0

        times = data["time"].astype(str).tolist()
        opens = data["open"].to_numpy(dtype=float)
        highs = data["high"].to_numpy(dtype=float)
        lows = data["low"].to_numpy(dtype=float)
        closes = data["close"].to_numpy(dtype=float)
        atr_values = (
            data["atr"].to_numpy(dtype=float)
            if "atr" in data.columns
            else np.full(len(data), np.nan)
        )

        for index in range(warmup, len(data)):
            bar_time = times[index]

            # --- 1) fill a pending entry at this bar's open --------------
            if pending is not None and open_trade is None:
                direction = pending["direction"]
                entry = float(opens[index] + spread if direction == "BUY" else opens[index])
                distance = pending["distance"]
                stop = stop_loss_price(entry, direction, distance, spec)
                target = take_profit_price(entry, direction, distance, cfg.min_risk_reward, spec)
                per_lot = loss_per_lot(spec, distance)
                sizing = calculate_lot_size(
                    balance=balance,
                    risk_percent=risk_percent,
                    stop_distance=distance,
                    spec=spec,
                    max_lot=cfg.max_lot_size,
                    min_lot=cfg.min_lot_size,
                    money_per_lot=per_lot,
                )
                if sizing["within_risk"] and sizing["volume"] > 0:
                    open_trade = _OpenTrade(
                        direction=direction,
                        entry_index=index,
                        entry_time=bar_time,
                        entry_price=entry,
                        volume=sizing["volume"],
                        stop_loss=stop,
                        take_profit=target,
                        initial_sl=stop,
                        money_per_lot_unit=per_lot / distance if distance else 0.0,
                        reason=pending["reason"],
                    )
                pending = None

            # --- 2) manage / close the open trade ----------------------
            if open_trade is not None:
                exit_price: float | None = None
                exit_reason = ""
                is_buy = open_trade.direction == "BUY"

                # Stop first: the pessimistic assumption when a bar covers both.
                if is_buy and lows[index] <= open_trade.stop_loss:
                    exit_price, exit_reason = open_trade.stop_loss, "SL"
                elif not is_buy and highs[index] >= open_trade.stop_loss:
                    exit_price, exit_reason = open_trade.stop_loss, "SL"
                elif is_buy and highs[index] >= open_trade.take_profit:
                    exit_price, exit_reason = open_trade.take_profit, "TP"
                elif not is_buy and lows[index] <= open_trade.take_profit:
                    exit_price, exit_reason = open_trade.take_profit, "TP"
                elif (
                    cfg.max_holding_hours > 0
                    and self._hours_between(times[open_trade.entry_index], bar_time) >= cfg.max_holding_hours
                ):
                    exit_price, exit_reason = float(closes[index]), "max_holding_time"

                if exit_price is not None:
                    exit_price = float(exit_price)
                    move = exit_price - open_trade.entry_price
                    if not is_buy:
                        move = -move
                    profit = float(move * open_trade.money_per_lot_unit * open_trade.volume)
                    balance += profit
                    # A stop that was moved by break-even/trailing is not the
                    # original stop loss - label it so the trade list is honest.
                    if exit_reason == "SL" and abs(open_trade.stop_loss - open_trade.initial_sl) > spec.point / 2:
                        exit_reason = "break_even" if open_trade.break_even_done and not cfg.trailing_stop_enabled else "trailing_stop"
                    risk_money = (
                        open_trade.risk_distance * open_trade.money_per_lot_unit * open_trade.volume
                    )
                    trades.append(
                        BacktestTrade(
                            ticket=ticket,
                            direction=open_trade.direction,
                            entry_time=open_trade.entry_time,
                            entry_price=open_trade.entry_price,
                            exit_time=bar_time,
                            exit_price=exit_price,
                            volume=open_trade.volume,
                            stop_loss=open_trade.initial_sl,
                            take_profit=open_trade.take_profit,
                            profit=profit,
                            r_multiple=profit / risk_money if risk_money else 0.0,
                            exit_reason=exit_reason,
                            balance_after=balance,
                            bars_held=index - open_trade.entry_index,
                            reason=open_trade.reason,
                        )
                    )
                    ticket += 1
                    open_trade = None

                    peak_balance = max(peak_balance, balance)
                    drawdown = peak_balance - balance
                    if drawdown > max_drawdown_money:
                        max_drawdown_money = drawdown
                        max_drawdown_percent = drawdown / peak_balance * 100 if peak_balance else 0.0
                else:
                    self._apply_position_management(open_trade, closes[index], atr_values[index], spec)

            # --- 3) look for a new signal on this closed bar -----------
            if open_trade is None and pending is None:
                window = data.iloc[: index + 1]
                signal = strategy.generate_signal(window, symbol, timeframe)
                if signal.is_actionable and signal.atr and signal.atr > 0:
                    distance = apply_min_stop_distance(
                        atr_stop_distance(float(signal.atr), cfg.atr_sl_multiplier), spec
                    )
                    pending = {
                        "direction": signal.direction,
                        "distance": distance,
                        "reason": signal.reason,
                    }

            floating = 0.0
            if open_trade is not None:
                move = closes[index] - open_trade.entry_price
                if open_trade.direction == "SELL":
                    move = -move
                floating = move * open_trade.money_per_lot_unit * open_trade.volume
            equity_curve.append(
                {
                    "time": bar_time,
                    "balance": round(float(balance), 2),
                    "equity": round(float(balance + floating), 2),
                }
            )

        return self._metrics(trades, equity_curve, initial_balance, balance,
                             max_drawdown_money, max_drawdown_percent)

    # ------------------------------------------------------------------
    def _apply_position_management(
        self, trade: _OpenTrade, close: float, atr_value: float, spec: SymbolSpec
    ) -> None:
        """Break-even and trailing, evaluated on the bar close."""
        cfg = self.config
        risk = trade.risk_distance
        if risk <= 0:
            return
        move = close - trade.entry_price
        if trade.direction == "SELL":
            move = -move
        r_multiple = move / risk

        if cfg.break_even_enabled and not trade.break_even_done and r_multiple >= cfg.break_even_trigger_r:
            offset = cfg.break_even_offset_points * spec.point
            new_sl = (
                trade.entry_price + offset if trade.direction == "BUY" else trade.entry_price - offset
            )
            if self._is_better_stop(trade, new_sl):
                trade.stop_loss = spec.normalize_price(new_sl)
                trade.break_even_done = True

        if (
            cfg.trailing_stop_enabled
            and r_multiple >= cfg.trailing_start_r
            and atr_value == atr_value  # not NaN
            and atr_value > 0
        ):
            distance = max(
                atr_value * cfg.trailing_distance_atr,
                spec.stops_level_points * spec.point + spec.point,
            )
            new_sl = close - distance if trade.direction == "BUY" else close + distance
            if self._is_better_stop(trade, new_sl):
                trade.stop_loss = spec.normalize_price(new_sl)

    @staticmethod
    def _is_better_stop(trade: _OpenTrade, new_sl: float) -> bool:
        return new_sl > trade.stop_loss if trade.direction == "BUY" else new_sl < trade.stop_loss

    @staticmethod
    def _hours_between(start: str, end: str) -> float:
        try:
            return (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 3600.0
        except (ValueError, TypeError):
            return 0.0

    # ------------------------------------------------------------------
    def _metrics(
        self,
        trades: list[BacktestTrade],
        equity_curve: list[dict[str, Any]],
        initial_balance: float,
        final_balance: float,
        max_drawdown_money: float,
        max_drawdown_percent: float,
    ) -> dict[str, Any]:
        wins = [t for t in trades if t.profit > 0]
        losses = [t for t in trades if t.profit < 0]
        gross_profit = sum(t.profit for t in wins)
        gross_loss = abs(sum(t.profit for t in losses))
        total = len(trades)

        # Intra-trade drawdown from the equity curve (includes floating P/L).
        peak = initial_balance
        equity_dd_money = 0.0
        equity_dd_percent = 0.0
        for point in equity_curve:
            peak = max(peak, point["equity"])
            drop = peak - point["equity"]
            if drop > equity_dd_money:
                equity_dd_money = drop
                equity_dd_percent = drop / peak * 100 if peak else 0.0

        average_win = gross_profit / len(wins) if wins else 0.0
        average_loss = gross_loss / len(losses) if losses else 0.0
        win_rate = len(wins) / total * 100 if total else 0.0
        expectancy = (
            (win_rate / 100 * average_win) - ((1 - win_rate / 100) * average_loss) if total else 0.0
        )

        return {
            "total_trades": total,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(win_rate, 2),
            "net_profit": round(final_balance - initial_balance, 2),
            "net_profit_percent": round(
                (final_balance - initial_balance) / initial_balance * 100 if initial_balance else 0.0, 2
            ),
            "final_balance": round(final_balance, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            # None means "no losing trades at all" - the UI renders it as INF.
            # (float('inf') is not valid JSON, so it must not leak into the payload.)
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else (
                None if gross_profit > 0 else 0.0
            ),
            "max_drawdown": round(max(max_drawdown_percent, equity_dd_percent), 2),
            "max_drawdown_money": round(max(max_drawdown_money, equity_dd_money), 2),
            "average_win": round(average_win, 2),
            "average_loss": round(average_loss, 2),
            "largest_win": round(max((t.profit for t in wins), default=0.0), 2),
            "largest_loss": round(min((t.profit for t in losses), default=0.0), 2),
            "expectancy": round(expectancy, 2),
            "average_bars_held": round(
                sum(t.bars_held for t in trades) / total, 1
            ) if total else 0.0,
            "equity_curve": equity_curve,
            "trades": [t.to_dict() for t in trades],
        }
