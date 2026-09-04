"""Indicator correctness - values are checked against published references."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.indicators import (
    add_indicators, atr, bollinger_bands, crossed_above, crossed_below,
    ema, latest_values, macd, rsi, sma, true_range,
)

# Wilder's own worked example ("New Concepts in Technical Trading Systems").
WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
]


def test_rsi_matches_wilders_reference_values():
    values = rsi(pd.Series(WILDER_CLOSES), 14)
    assert values.iloc[14] == pytest.approx(70.46, abs=0.01)
    assert values.iloc[15] == pytest.approx(66.25, abs=0.01)


def test_rsi_has_no_value_before_the_seed_period():
    values = rsi(pd.Series(WILDER_CLOSES), 14)
    assert values.iloc[:14].isna().all()
    assert not np.isnan(values.iloc[14])


def test_rsi_is_bounded_between_zero_and_hundred():
    rng = np.random.default_rng(3)
    values = rsi(pd.Series(100 + np.cumsum(rng.normal(0, 1, 500))), 14).dropna()
    assert values.between(0, 100).all()


def test_rsi_of_a_pure_uptrend_is_one_hundred():
    assert rsi(pd.Series(np.arange(1, 40, dtype=float)), 14).iloc[-1] == pytest.approx(100.0)


def test_ema_matches_manual_recursion():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ema(series, 3)
    # seed = SMA(1,2,3) = 2, then value += (price - value) * 2/(3+1)
    expected = 2.0
    for price in (4.0, 5.0):
        expected += (price - expected) * 0.5
    assert result.iloc[-1] == pytest.approx(expected)
    assert result.iloc[:2].isna().all()


def test_sma_is_the_arithmetic_mean():
    series = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert sma(series, 4).iloc[-1] == pytest.approx(25.0)


def test_true_range_uses_the_previous_close():
    high = pd.Series([10.0, 12.0])
    low = pd.Series([9.0, 11.0])
    close = pd.Series([9.5, 11.5])
    # bar 2: max(12-11, |12-9.5|, |11-9.5|) = 2.5
    assert true_range(high, low, close).iloc[1] == pytest.approx(2.5)


def test_atr_is_positive_and_smoothed():
    rng = np.random.default_rng(11)
    close = pd.Series(1.10 + np.cumsum(rng.normal(0, 0.001, 300)))
    values = atr(close + 0.001, close - 0.001, close, 14).dropna()
    assert (values > 0).all()
    assert values.std() < values.mean()  # smoothing removes the spikes


def test_macd_histogram_is_the_difference_of_its_lines():
    close = pd.Series(np.linspace(1, 10, 120))
    frame = macd(close)
    assert frame["histogram"].iloc[-1] == pytest.approx(
        frame["macd"].iloc[-1] - frame["signal"].iloc[-1]
    )


def test_bollinger_bands_are_symmetric_around_the_middle():
    close = pd.Series(np.linspace(1, 30, 60))
    bands = bollinger_bands(close, 20, 2.0)
    upper_gap = bands["upper"].iloc[-1] - bands["middle"].iloc[-1]
    lower_gap = bands["middle"].iloc[-1] - bands["lower"].iloc[-1]
    assert upper_gap == pytest.approx(lower_gap)
    assert bands["upper"].iloc[-1] > bands["lower"].iloc[-1]


def test_crossovers_fire_exactly_once_on_the_crossing_bar():
    fast = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0])
    slow = pd.Series([2.0, 2.0, 2.0, 2.0, 2.0])
    assert crossed_above(fast, slow).tolist() == [False, False, True, False, False]
    assert crossed_below(fast, slow).tolist() == [False, False, False, False, True]


def test_add_indicators_attaches_the_full_set(trending_frame):
    enriched = add_indicators(trending_frame)
    for column in ("ema_fast", "ema_slow", "rsi", "atr", "macd", "bb_upper", "bb_lower"):
        assert column in enriched.columns
    assert len(enriched) == len(trending_frame)


def test_latest_values_converts_nan_to_none():
    frame = pd.DataFrame({"time": pd.to_datetime(["2024-01-01"], utc=True), "close": [1.0], "atr": [np.nan]})
    values = latest_values(frame)
    assert values["atr"] is None
    assert values["close"] == 1.0
