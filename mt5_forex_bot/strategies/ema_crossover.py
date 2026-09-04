"""EMA crossover strategy - the bot's default.

BUY  : EMA(fast) crosses **above** EMA(slow) on a closed candle,
       optional filter RSI < ``rsi_buy_max`` (default 70, i.e. not overbought).
SELL : EMA(fast) crosses **below** EMA(slow) on a closed candle,
       optional filter RSI > ``rsi_sell_min`` (default 30, i.e. not oversold).

Only closed candles are evaluated: the forming candle is stripped by the engine
before ``generate_signal`` is called, so a cross that appears mid-bar and
disappears again can never trigger a trade.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.indicators import atr, crossed_above, crossed_below, ema, rsi
from strategies.base_strategy import (
    BUY,
    SELL,
    BaseStrategy,
    ParamSpec,
    Signal,
    register_strategy,
)


@register_strategy
class EmaCrossoverStrategy(BaseStrategy):
    name = "ema_crossover"
    display_name = "EMA Crossover"
    description = (
        "Trades the crossover of a fast and a slow EMA on closed candles, with an "
        "optional RSI filter that blocks buying into an overbought market and "
        "selling into an oversold one. Stop distance comes from ATR."
    )

    @classmethod
    def param_specs(cls) -> list[ParamSpec]:
        return [
            ParamSpec("ema_fast", "EMA Fast", "int", 20, 2, 500, 1, help="Fast EMA period"),
            ParamSpec("ema_slow", "EMA Slow", "int", 50, 3, 1000, 1, help="Slow EMA period"),
            ParamSpec(
                "use_rsi_filter", "Use RSI filter", "bool", True,
                help="Skip signals when RSI says the market is already stretched",
            ),
            ParamSpec("rsi_period", "RSI Period", "int", 14, 2, 100, 1),
            ParamSpec(
                "rsi_buy_max", "RSI Buy below", "float", 70.0, 1, 100, 0.5,
                help="Only buy while RSI is below this level",
            ),
            ParamSpec(
                "rsi_sell_min", "RSI Sell above", "float", 30.0, 0, 99, 0.5,
                help="Only sell while RSI is above this level",
            ),
            ParamSpec("atr_period", "ATR Period", "int", 14, 2, 100, 1, help="ATR used for stop distance"),
        ]

    # ------------------------------------------------------------------
    @property
    def required_bars(self) -> int:
        return (
            max(
                int(self.params["ema_slow"]),
                int(self.params["rsi_period"]),
                int(self.params["atr_period"]),
            )
            + 30
        )

    def prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        data = frame.copy()
        if data.empty:
            return data
        data["ema_fast"] = ema(data["close"], int(self.params["ema_fast"]))
        data["ema_slow"] = ema(data["close"], int(self.params["ema_slow"]))
        data["rsi"] = rsi(data["close"], int(self.params["rsi_period"]))
        data["atr"] = atr(data["high"], data["low"], data["close"], int(self.params["atr_period"]))
        data["cross_up"] = crossed_above(data["ema_fast"], data["ema_slow"])
        data["cross_down"] = crossed_below(data["ema_fast"], data["ema_slow"])
        return data

    def generate_signal(self, frame: pd.DataFrame, symbol: str, timeframe: str) -> Signal:
        if frame.empty or len(frame) < self.required_bars:
            return self.no_signal(
                symbol, timeframe,
                f"Not enough history ({len(frame)}/{self.required_bars} bars)", frame,
            )

        data = frame if "cross_up" in frame.columns else self.prepare(frame)
        last = data.iloc[-1]

        if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]) or pd.isna(last["atr"]):
            return self.no_signal(symbol, timeframe, "Indicators still warming up", data)

        snapshot: dict[str, Any] = {
            "ema_fast": round(float(last["ema_fast"]), 6),
            "ema_slow": round(float(last["ema_slow"]), 6),
            "rsi": None if pd.isna(last["rsi"]) else round(float(last["rsi"]), 2),
            "atr": round(float(last["atr"]), 6),
            "close": round(float(last["close"]), 6),
        }
        base = {
            "symbol": symbol,
            "timeframe": timeframe,
            "price": float(last["close"]),
            "strategy": self.name,
            "atr": float(last["atr"]),
            "bar_time": str(last["time"]),
            "indicators": snapshot,
        }
        fast_period = int(self.params["ema_fast"])
        slow_period = int(self.params["ema_slow"])
        use_rsi = bool(self.params["use_rsi_filter"])
        rsi_value = None if pd.isna(last["rsi"]) else float(last["rsi"])

        if bool(last["cross_up"]):
            if use_rsi and rsi_value is not None and rsi_value >= float(self.params["rsi_buy_max"]):
                return Signal(
                    direction="NONE",
                    reason=(
                        f"EMA{fast_period} crossed above EMA{slow_period} but the RSI filter "
                        f"blocked it (RSI {rsi_value:.1f} >= {float(self.params['rsi_buy_max']):.0f})"
                    ),
                    **base,
                )
            return Signal(
                direction=BUY,
                reason=(
                    f"EMA{fast_period} crossed above EMA{slow_period} on the closed candle"
                    + (f" (RSI {rsi_value:.1f})" if rsi_value is not None else "")
                ),
                **base,
            )

        if bool(last["cross_down"]):
            if use_rsi and rsi_value is not None and rsi_value <= float(self.params["rsi_sell_min"]):
                return Signal(
                    direction="NONE",
                    reason=(
                        f"EMA{fast_period} crossed below EMA{slow_period} but the RSI filter "
                        f"blocked it (RSI {rsi_value:.1f} <= {float(self.params['rsi_sell_min']):.0f})"
                    ),
                    **base,
                )
            return Signal(
                direction=SELL,
                reason=(
                    f"EMA{fast_period} crossed below EMA{slow_period} on the closed candle"
                    + (f" (RSI {rsi_value:.1f})" if rsi_value is not None else "")
                ),
                **base,
            )

        trend = "above" if float(last["ema_fast"]) > float(last["ema_slow"]) else "below"
        return Signal(
            direction="NONE",
            reason=f"No crossover - EMA{fast_period} is {trend} EMA{slow_period}",
            **base,
        )
