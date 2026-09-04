"""Technical indicators.

Implemented directly on top of pandas/numpy so the numbers are reproducible and
identical between the live engine and the backtester (no hidden library
version differences).  RSI and ATR use Wilder's smoothing, which is what
MetaTrader itself uses, so the values line up with the terminal's own
indicators.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# moving averages
# ----------------------------------------------------------------------
def _seeded_ewm(series: pd.Series, alpha: float, period: int) -> pd.Series:
    """Recursive smoothing seeded with the SMA of the first ``period`` values.

    MetaTrader (and TradingView) seed both EMA and Wilder's RMA with a simple
    moving average of the first ``period`` observations. Pandas' ``ewm`` seeds
    with the first observation instead, which shifts the curve for hundreds of
    bars - enough that the EMA drawn on the dashboard would not line up with the
    one drawn in the MT5 terminal. This helper does it the MetaTrader way.
    """
    values = pd.Series(series, dtype="float64")
    array = values.to_numpy(dtype="float64", copy=True)
    output = np.full(array.shape, np.nan)

    valid = ~np.isnan(array)
    start: int | None = None
    streak = 0
    for index in range(array.size):
        streak = streak + 1 if valid[index] else 0
        if streak == period:
            start = index
            break
    if start is None:
        return pd.Series(output, index=values.index)

    working = array[start:].copy()
    working[0] = float(np.mean(array[start - period + 1 : start + 1]))
    output[start:] = (
        pd.Series(working).ffill().ewm(alpha=alpha, adjust=False).mean().to_numpy()
    )
    return pd.Series(output, index=values.index)


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (SMA seeded, as MetaTrader calculates it)."""
    return _seeded_ewm(series, 2.0 / (period + 1.0), period)


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return pd.Series(series, dtype="float64").rolling(window=period, min_periods=period).mean()


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RMA - the smoothing used by RSI and ATR inside MetaTrader.

    Wilder seeds with the simple mean of the first ``period`` observations and
    then applies ``avg += (value - avg) / period``.
    """
    return _seeded_ewm(series, 1.0 / period, period)


# ----------------------------------------------------------------------
# oscillators
# ----------------------------------------------------------------------
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder)."""
    close = pd.Series(series, dtype="float64")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100 - (100 / (1 + rs))
    # A run without a single down bar is RSI 100 by definition.
    result[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    result[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return result


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD line, signal line and histogram."""
    close = pd.Series(series, dtype="float64")
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }
    )


# ----------------------------------------------------------------------
# volatility
# ----------------------------------------------------------------------
def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    high = pd.Series(high, dtype="float64")
    low = pd.Series(low, dtype="float64")
    close = pd.Series(close, dtype="float64")
    previous_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (Wilder). Drives every stop-loss distance."""
    return wilder_smooth(true_range(high, low, close), period)


def bollinger_bands(
    series: pd.Series, period: int = 20, deviations: float = 2.0
) -> pd.DataFrame:
    """Bollinger Bands (middle/upper/lower plus bandwidth and %B)."""
    close = pd.Series(series, dtype="float64")
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + deviations * std
    lower = middle - deviations * std
    width = (upper - lower) / middle.replace(0.0, np.nan)
    percent_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": width,
            "percent_b": percent_b,
        }
    )


# ----------------------------------------------------------------------
# crossovers
# ----------------------------------------------------------------------
def crossed_above(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """True on the bar where ``fast`` crosses from below to above ``slow``."""
    fast = pd.Series(fast, dtype="float64")
    slow = pd.Series(slow, dtype="float64")
    return (fast > slow) & (fast.shift(1) <= slow.shift(1))


def crossed_below(fast: pd.Series, slow: pd.Series) -> pd.Series:
    fast = pd.Series(fast, dtype="float64")
    slow = pd.Series(slow, dtype="float64")
    return (fast < slow) & (fast.shift(1) >= slow.shift(1))


# ----------------------------------------------------------------------
# convenience
# ----------------------------------------------------------------------
def add_indicators(
    frame: pd.DataFrame,
    ema_fast: int = 20,
    ema_slow: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    include_macd: bool = True,
    include_bollinger: bool = True,
) -> pd.DataFrame:
    """Attach the standard indicator set to an OHLCV frame (returns a copy)."""
    data = frame.copy()
    if data.empty:
        return data

    data["ema_fast"] = ema(data["close"], ema_fast)
    data["ema_slow"] = ema(data["close"], ema_slow)
    data["sma_fast"] = sma(data["close"], ema_fast)
    data["rsi"] = rsi(data["close"], rsi_period)
    data["atr"] = atr(data["high"], data["low"], data["close"], atr_period)

    if include_macd:
        macd_frame = macd(data["close"])
        data["macd"] = macd_frame["macd"]
        data["macd_signal"] = macd_frame["signal"]
        data["macd_hist"] = macd_frame["histogram"]

    if include_bollinger:
        bands = bollinger_bands(data["close"])
        data["bb_middle"] = bands["middle"]
        data["bb_upper"] = bands["upper"]
        data["bb_lower"] = bands["lower"]
        data["bb_width"] = bands["bandwidth"]

    return data


def latest_values(frame: pd.DataFrame, columns: list[str] | None = None) -> dict[str, Any]:
    """Last row of the given indicator columns as plain floats (NaN -> None)."""
    if frame.empty:
        return {}
    row = frame.iloc[-1]
    columns = columns or [
        c for c in frame.columns if c not in ("time", "spread", "real_volume", "tick_volume")
    ]
    values: dict[str, Any] = {}
    for column in columns:
        if column not in frame.columns:
            continue
        value = row[column]
        if isinstance(value, (pd.Timestamp,)):
            values[column] = value.isoformat()
        elif value is None or (isinstance(value, float) and np.isnan(value)):
            values[column] = None
        else:
            try:
                values[column] = round(float(value), 6)
            except (TypeError, ValueError):
                values[column] = str(value)
    return values
