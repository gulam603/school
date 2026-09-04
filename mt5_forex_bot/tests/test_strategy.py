"""Strategy contract and the EMA crossover signal rules."""

from __future__ import annotations

import pandas as pd
import pytest

from strategies import available_strategies, get_strategy, strategy_names
from strategies.base_strategy import BaseStrategy, Signal


@pytest.fixture
def strategy():
    return get_strategy(
        "ema_crossover",
        {"ema_fast": 5, "ema_slow": 10, "use_rsi_filter": False, "rsi_period": 5, "atr_period": 5},
    )


def test_the_default_strategy_is_registered():
    assert "ema_crossover" in strategy_names()
    assert any(info["name"] == "ema_crossover" for info in available_strategies())


def test_unknown_strategies_are_rejected():
    with pytest.raises(ValueError, match="Unknown strategy"):
        get_strategy("does_not_exist")


def test_strategy_exposes_its_parameters_for_the_ui():
    info = next(item for item in available_strategies() if item["name"] == "ema_crossover")
    names = {param["name"] for param in info["params"]}
    assert {"ema_fast", "ema_slow", "use_rsi_filter", "rsi_period", "atr_period"} <= names


def test_unknown_parameters_are_ignored(strategy):
    instance = get_strategy("ema_crossover", {"ema_fast": 7, "not_a_param": 99})
    assert instance.params["ema_fast"] == 7
    assert "not_a_param" not in instance.params


def test_prepare_adds_the_crossover_columns(strategy, trending_frame):
    prepared = strategy.prepare(trending_frame)
    for column in ("ema_fast", "ema_slow", "rsi", "atr", "cross_up", "cross_down"):
        assert column in prepared.columns


def test_a_bullish_crossover_produces_a_buy(strategy, trending_frame):
    prepared = strategy.prepare(trending_frame)
    cross_index = prepared.index[prepared["cross_up"]][0]
    signal = strategy.generate_signal(prepared.iloc[: cross_index + 1], "EURUSD", "H1")
    assert signal.direction == "BUY"
    assert signal.is_actionable
    assert signal.atr and signal.atr > 0
    assert "crossed above" in signal.reason


def test_a_bearish_crossover_produces_a_sell(strategy):
    close = pd.Series(list(pd.Series(range(70)) / 1000 + 1.05) + list(1.12 - pd.Series(range(60)) / 1000))
    frame = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=len(close), freq="h", tz="UTC"),
        "open": close, "high": close + 0.001, "low": close - 0.001, "close": close,
        "tick_volume": 100, "spread": 10, "real_volume": 0,
    })
    prepared = strategy.prepare(frame)
    down_indexes = prepared.index[prepared["cross_down"]]
    assert len(down_indexes) > 0
    signal = strategy.generate_signal(prepared.iloc[: down_indexes[0] + 1], "EURUSD", "H1")
    assert signal.direction == "SELL"


def test_no_signal_without_a_crossover(strategy, trending_frame):
    prepared = strategy.prepare(trending_frame)
    signal = strategy.generate_signal(prepared, "EURUSD", "H1")
    assert signal.direction == "NONE"
    assert signal.is_actionable is False
    assert "No crossover" in signal.reason


def test_the_rsi_filter_blocks_an_overbought_buy(trending_frame):
    filtered = get_strategy(
        "ema_crossover",
        {"ema_fast": 5, "ema_slow": 10, "use_rsi_filter": True, "rsi_buy_max": 30,
         "rsi_period": 5, "atr_period": 5},
    )
    prepared = filtered.prepare(trending_frame)
    cross_index = prepared.index[prepared["cross_up"]][0]
    signal = filtered.generate_signal(prepared.iloc[: cross_index + 1], "EURUSD", "H1")
    assert signal.direction == "NONE"
    assert "RSI filter" in signal.reason


def test_a_short_history_produces_no_signal(strategy, trending_frame):
    signal = strategy.generate_signal(strategy.prepare(trending_frame.head(10)), "EURUSD", "H1")
    assert signal.direction == "NONE"
    assert "Not enough history" in signal.reason


def test_required_bars_covers_the_longest_period():
    strategy = get_strategy("ema_crossover", {"ema_slow": 200, "rsi_period": 14, "atr_period": 14})
    assert strategy.required_bars > 200


def test_signal_serialises_for_the_api():
    payload = Signal(symbol="EURUSD", timeframe="H1", direction="BUY", price=1.1).to_dict()
    assert payload["symbol"] == "EURUSD"
    assert payload["direction"] == "BUY"


def test_strategies_cannot_place_orders():
    """A strategy is a pure signal function - it must not touch execution."""
    source = BaseStrategy.__module__
    assert "order" not in source
    for name in strategy_names():
        instance = get_strategy(name)
        assert not hasattr(instance, "order_send")
        assert not hasattr(instance, "open_position")
