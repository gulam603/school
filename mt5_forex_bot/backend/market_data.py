"""Live quotes, historical candles and symbol contract specifications.

Everything here reads from the real MT5 terminal - there is no synthetic price
source anywhere in the bot.  Candles are cached for a fraction of the bar
duration so a five second strategy loop does not hammer the terminal, while a
new bar is always picked up promptly.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.logger import get_logger
from backend.mt5_connection import (
    MT5Connection,
    get_connection,
    timeframe_seconds,
    timeframe_to_mt5,
)
from backend.mt5_errors import InvalidSymbolError, MarketClosedError, MT5Error
from backend.symbols import SymbolResolver, get_resolver

log = get_logger("market_data")

OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


@dataclass(frozen=True)
class SymbolSpec:
    """The broker's contract specification for one instrument."""

    name: str
    canonical: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int
    freeze_level_points: int
    spread_points: int
    trade_mode: int
    currency_profit: str
    currency_margin: str

    @property
    def tradable(self) -> bool:
        # 0 = disabled, 1 = long only, 2 = short only, 3 = close only, 4 = full
        return self.trade_mode == 4

    @property
    def pip_size(self) -> float:
        """One pip = 10 points on a 3/5 digit quote, 1 point otherwise."""
        return self.point * 10 if self.digits in (3, 5) else self.point

    def normalize_price(self, price: float) -> float:
        return round(float(price), self.digits)

    def normalize_volume(self, volume: float) -> float:
        """Snap a volume onto the broker's lot step and bounds."""
        step = self.volume_step or 0.01
        steps = round(float(volume) / step)
        snapped = round(steps * step, 8)
        snapped = max(self.volume_min, min(self.volume_max, snapped))
        decimals = max(0, len(str(step).split(".")[-1]) if "." in str(step) else 0)
        return round(snapped, decimals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "canonical": self.canonical,
            "digits": self.digits,
            "point": self.point,
            "tick_size": self.tick_size,
            "tick_value": self.tick_value,
            "contract_size": self.contract_size,
            "volume_min": self.volume_min,
            "volume_max": self.volume_max,
            "volume_step": self.volume_step,
            "stops_level_points": self.stops_level_points,
            "spread_points": self.spread_points,
            "pip_size": self.pip_size,
            "tradable": self.tradable,
            "currency_profit": self.currency_profit,
        }


class MarketData:
    """Reads prices, candles and specifications from the MT5 terminal."""

    def __init__(
        self,
        connection: MT5Connection | None = None,
        resolver: SymbolResolver | None = None,
    ) -> None:
        self._conn = connection or get_connection()
        self._resolver = resolver or get_resolver()
        self._lock = threading.RLock()
        self._rates_cache: dict[tuple[str, str, int], tuple[float, pd.DataFrame]] = {}
        self._spec_cache: dict[str, tuple[float, SymbolSpec]] = {}

    # ------------------------------------------------------------------
    def resolve(self, symbol: str, prefix: str = "", suffix: str = "") -> str:
        return self._resolver.resolve(symbol, prefix, suffix)

    def to_canonical(self, broker_symbol: str) -> str:
        """Reverse lookup: the broker's name -> the canonical symbol name."""
        return self._resolver.to_canonical(broker_symbol)

    def invalidate(self, symbol: str | None = None) -> None:
        with self._lock:
            if symbol is None:
                self._rates_cache.clear()
                self._spec_cache.clear()
                return
            for key in [k for k in self._rates_cache if k[0] == symbol]:
                self._rates_cache.pop(key, None)
            self._spec_cache.pop(symbol, None)

    # ------------------------------------------------------------------
    # specifications
    # ------------------------------------------------------------------
    def symbol_spec(self, broker_symbol: str, ttl: float = 300.0) -> SymbolSpec:
        """Contract specification, cached for five minutes."""
        with self._lock:
            cached = self._spec_cache.get(broker_symbol)
            if cached and time.time() - cached[0] < ttl:
                return cached[1]

        info = self._conn.symbol_info(broker_symbol)
        if info is None:
            raise InvalidSymbolError(f"Symbol '{broker_symbol}' is unknown to the broker")
        if not getattr(info, "visible", False):
            self._conn.symbol_select(broker_symbol, True)
            info = self._conn.symbol_info(broker_symbol) or info

        spec = SymbolSpec(
            name=str(info.name),
            canonical=self._resolver.to_canonical(str(info.name)),
            digits=int(info.digits),
            point=float(info.point),
            tick_size=float(getattr(info, "trade_tick_size", info.point) or info.point),
            tick_value=float(getattr(info, "trade_tick_value", 0.0)),
            contract_size=float(getattr(info, "trade_contract_size", 100_000.0)),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            stops_level_points=int(getattr(info, "trade_stops_level", 0)),
            freeze_level_points=int(getattr(info, "trade_freeze_level", 0)),
            spread_points=int(getattr(info, "spread", 0)),
            trade_mode=int(getattr(info, "trade_mode", 4)),
            currency_profit=str(getattr(info, "currency_profit", "")),
            currency_margin=str(getattr(info, "currency_margin", "")),
        )
        with self._lock:
            self._spec_cache[broker_symbol] = (time.time(), spec)
        return spec

    # ------------------------------------------------------------------
    # quotes
    # ------------------------------------------------------------------
    def get_tick(self, broker_symbol: str) -> dict[str, Any]:
        """Current bid/ask. Raises ``MarketClosedError`` when quotes are stale."""
        tick = self._conn.symbol_info_tick(broker_symbol)
        if tick is None:
            raise InvalidSymbolError(f"No quotes available for '{broker_symbol}'")
        bid = float(tick.bid)
        ask = float(tick.ask)
        if bid <= 0 or ask <= 0:
            raise MarketClosedError(f"Market for '{broker_symbol}' appears to be closed (no quotes)")
        spec = self.symbol_spec(broker_symbol)
        return {
            "symbol": broker_symbol,
            "canonical": spec.canonical,
            "bid": bid,
            "ask": ask,
            "last": float(getattr(tick, "last", 0.0)) or bid,
            "spread": round(ask - bid, spec.digits),
            "spread_points": int(round((ask - bid) / spec.point)) if spec.point else 0,
            "digits": spec.digits,
            "time": datetime.fromtimestamp(int(tick.time), tz=timezone.utc).isoformat(),
            "timestamp": int(tick.time),
        }

    def market_watch(self, canonical_symbols: list[str], prefix: str = "", suffix: str = "") -> list[dict[str, Any]]:
        """Quote table for the Market Watch page; failures degrade per-row."""
        rows: list[dict[str, Any]] = []
        for canonical in canonical_symbols:
            row: dict[str, Any] = {"symbol": canonical, "broker_symbol": None, "error": None}
            try:
                broker_symbol = self.resolve(canonical, prefix, suffix)
                row["broker_symbol"] = broker_symbol
                tick = self.get_tick(broker_symbol)
                spec = self.symbol_spec(broker_symbol)
                daily = self.daily_change(broker_symbol)
                row.update(
                    {
                        "bid": tick["bid"],
                        "ask": tick["ask"],
                        "spread": tick["spread"],
                        "spread_points": tick["spread_points"],
                        "digits": spec.digits,
                        "time": tick["time"],
                        "change_percent": daily.get("change_percent"),
                        "day_high": daily.get("high"),
                        "day_low": daily.get("low"),
                        "tradable": spec.tradable,
                    }
                )
            except MT5Error as exc:
                row["error"] = exc.message
            rows.append(row)
        return rows

    def daily_change(self, broker_symbol: str) -> dict[str, Any]:
        """Today's open/high/low and the percentage change from the D1 open."""
        try:
            frame = self.get_rates(broker_symbol, "D1", 2, use_cache=True, cache_ttl=60.0)
        except MT5Error:
            return {}
        if frame.empty:
            return {}
        today = frame.iloc[-1]
        open_price = float(today["open"])
        close_price = float(today["close"])
        change = close_price - open_price
        return {
            "open": open_price,
            "high": float(today["high"]),
            "low": float(today["low"]),
            "change": round(change, 6),
            "change_percent": round(change / open_price * 100, 3) if open_price else 0.0,
        }

    # ------------------------------------------------------------------
    # candles
    # ------------------------------------------------------------------
    def get_rates(
        self,
        broker_symbol: str,
        timeframe: str,
        count: int = 500,
        use_cache: bool = True,
        cache_ttl: float | None = None,
    ) -> pd.DataFrame:
        """Latest ``count`` closed+forming candles as a DataFrame."""
        timeframe = timeframe.upper()
        key = (broker_symbol, timeframe, count)
        ttl = cache_ttl if cache_ttl is not None else min(15.0, timeframe_seconds(timeframe) / 20)

        if use_cache:
            with self._lock:
                cached = self._rates_cache.get(key)
                if cached and time.time() - cached[0] < ttl:
                    return cached[1].copy()

        rates = self._conn.copy_rates_from_pos(broker_symbol, timeframe_to_mt5(timeframe), 0, count)
        frame = self._to_frame(rates, broker_symbol, timeframe)

        with self._lock:
            self._rates_cache[key] = (time.time(), frame)
        return frame.copy()

    def get_rates_range(
        self, broker_symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Candles between two datetimes - used by the backtester."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        rates = self._conn.copy_rates_range(
            broker_symbol, timeframe_to_mt5(timeframe.upper()), start, end
        )
        return self._to_frame(rates, broker_symbol, timeframe)

    def _to_frame(self, rates: Any, broker_symbol: str, timeframe: str) -> pd.DataFrame:
        if rates is None or len(rates) == 0:
            code, description = self._conn.last_error()
            log.warning(
                f"No candles returned for {broker_symbol} {timeframe} "
                f"({description or 'empty history'})"
            )
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        frame = pd.DataFrame(rates)
        if "time" not in frame.columns:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        for column in ("open", "high", "low", "close"):
            frame[column] = frame[column].astype(float)
        for column in ("tick_volume", "spread", "real_volume"):
            if column not in frame.columns:
                frame[column] = 0
        frame = frame[OHLCV_COLUMNS].reset_index(drop=True)
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        return frame

    # ------------------------------------------------------------------
    def closed_bars(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Drop the still-forming candle - signals only act on closed bars."""
        return frame.iloc[:-1] if len(frame) > 1 else frame

    def is_market_open(self, broker_symbol: str, max_quote_age_seconds: int = 180) -> bool:
        """Heuristic: quotes must exist and be recent, and trading enabled."""
        try:
            spec = self.symbol_spec(broker_symbol)
            if not spec.tradable:
                return False
            tick = self._conn.symbol_info_tick(broker_symbol)
            if tick is None or float(tick.bid) <= 0:
                return False
            age = time.time() - int(tick.time)
            return age <= max_quote_age_seconds
        except MT5Error:
            return False

    def chart_payload(
        self,
        canonical_symbol: str,
        timeframe: str,
        count: int = 300,
        prefix: str = "",
        suffix: str = "",
        ema_fast: int = 20,
        ema_slow: int = 50,
    ) -> dict[str, Any]:
        """Candles + EMA overlays formatted for the dashboard chart."""
        from backend.indicators import ema  # local import avoids a cycle

        broker_symbol = self.resolve(canonical_symbol, prefix, suffix)
        frame = self.get_rates(broker_symbol, timeframe, count)
        if frame.empty:
            return {
                "symbol": canonical_symbol,
                "broker_symbol": broker_symbol,
                "timeframe": timeframe,
                "candles": [],
                "ema_fast": [],
                "ema_slow": [],
                "digits": 5,
            }

        spec = self.symbol_spec(broker_symbol)
        fast = ema(frame["close"], ema_fast)
        slow = ema(frame["close"], ema_slow)
        times = (frame["time"].astype("int64") // 10**9).tolist()

        candles = [
            {
                "time": int(t),
                "open": float(o),
                "high": float(h),
                "low": float(low_),
                "close": float(c),
                "volume": int(v),
            }
            for t, o, h, low_, c, v in zip(
                times, frame["open"], frame["high"], frame["low"], frame["close"], frame["tick_volume"]
            )
        ]
        fast_series = [
            {"time": int(t), "value": round(float(v), spec.digits)}
            for t, v in zip(times, fast)
            if not np.isnan(v)
        ]
        slow_series = [
            {"time": int(t), "value": round(float(v), spec.digits)}
            for t, v in zip(times, slow)
            if not np.isnan(v)
        ]
        return {
            "symbol": canonical_symbol,
            "broker_symbol": broker_symbol,
            "timeframe": timeframe,
            "digits": spec.digits,
            "candles": candles,
            "ema_fast": fast_series,
            "ema_slow": slow_series,
            "ema_fast_period": ema_fast,
            "ema_slow_period": ema_slow,
            "last_price": candles[-1]["close"] if candles else None,
        }


_market_data: MarketData | None = None
_md_lock = threading.Lock()


def get_market_data() -> MarketData:
    global _market_data
    if _market_data is None:
        with _md_lock:
            if _market_data is None:
                _market_data = MarketData()
    return _market_data
