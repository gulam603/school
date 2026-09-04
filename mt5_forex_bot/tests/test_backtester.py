"""Backtester: metrics, risk compliance and the conservative fill model."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from backend.backtester import Backtester
from backend.mt5_errors import MT5Error


def price_frame(bars: int = 2500, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.0006, bars) + np.sin(np.linspace(0, 24, bars)) * 0.00035
    close = 1.10 + np.cumsum(steps)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0004, bars))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0004, bars))
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=bars, freq="h", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
        "tick_volume": 100, "spread": 10, "real_volume": 0,
    })


class FakeMarket:
    """Serves historical candles; everything else in the backtest is real code."""

    def __init__(self, spec, frame: pd.DataFrame):
        self.spec = spec
        self.frame = frame

    def resolve(self, symbol, prefix="", suffix=""):
        return symbol

    def symbol_spec(self, _symbol, ttl=300.0):
        return self.spec

    def get_rates_range(self, _symbol, _timeframe, _start, _end):
        return self.frame


@pytest.fixture
def backtester(config, database, eurusd_spec):
    return Backtester(config, market_data=FakeMarket(eurusd_spec, price_frame()), database=database)


def run(backtester, **kwargs):
    defaults = dict(
        symbol="EURUSD", timeframe="H1",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 6, 1, tzinfo=timezone.utc),
        initial_balance=10_000.0, risk_percent=1.0, save=False,
    )
    return backtester.run(**{**defaults, **kwargs})


def test_a_backtest_produces_every_required_metric(backtester):
    result = run(backtester)
    for key in (
        "total_trades", "winning_trades", "losing_trades", "win_rate", "net_profit",
        "max_drawdown", "profit_factor", "average_win", "average_loss",
        "largest_win", "largest_loss", "expectancy", "equity_curve", "trades",
    ):
        assert key in result


def test_the_disclaimer_travels_with_the_result(backtester):
    result = run(backtester)
    assert "does not guarantee future results" in result["disclaimer"]


def test_the_trade_counts_add_up(backtester):
    result = run(backtester)
    assert result["winning_trades"] + result["losing_trades"] <= result["total_trades"]
    assert len(result["trades"]) == result["total_trades"]


def test_win_rate_matches_the_trade_list(backtester):
    result = run(backtester)
    if result["total_trades"]:
        wins = sum(1 for trade in result["trades"] if trade["profit"] > 0)
        assert result["win_rate"] == pytest.approx(wins / result["total_trades"] * 100, abs=0.01)


def test_net_profit_matches_the_balance_change(backtester):
    result = run(backtester)
    assert result["net_profit"] == pytest.approx(result["final_balance"] - result["initial_balance"], abs=0.02)


def test_no_single_loss_exceeds_the_risk_percentage(backtester):
    """The whole point of the risk manager, verified over a full simulation."""
    result = run(backtester, risk_percent=1.0)
    for trade in result["trades"]:
        if trade["profit"] < 0:
            balance_before = trade["balance_after"] - trade["profit"]
            loss_percent = abs(trade["profit"]) / balance_before * 100
            assert loss_percent <= 1.05, f"trade {trade['ticket']} risked {loss_percent:.2f}%"


def test_halving_the_risk_halves_the_exposure(backtester):
    one_percent = run(backtester, risk_percent=1.0)
    half_percent = run(backtester, risk_percent=0.5)
    if one_percent["trades"] and half_percent["trades"]:
        assert half_percent["trades"][0]["volume"] < one_percent["trades"][0]["volume"]


def test_stop_and_target_respect_the_configured_risk_reward(backtester, config):
    result = run(backtester)
    for trade in result["trades"]:
        risk = abs(trade["entry_price"] - trade["stop_loss"])
        reward = abs(trade["take_profit"] - trade["entry_price"])
        assert reward / risk == pytest.approx(config.min_risk_reward, abs=0.05)


def test_losing_trades_are_capped_at_about_minus_one_r(backtester):
    result = run(backtester)
    for trade in result["trades"]:
        assert trade["r_multiple"] >= -1.05


def test_the_equity_curve_is_json_safe(backtester):
    import json

    result = run(backtester)
    encoded = json.dumps(result)          # would raise on numpy types or inf
    assert '"equity_curve"' in encoded
    assert "Infinity" not in encoded


def test_the_equity_curve_starts_at_the_initial_balance(backtester):
    result = run(backtester)
    assert result["equity_curve"][0]["balance"] == pytest.approx(result["initial_balance"])


def test_max_drawdown_is_never_negative(backtester):
    result = run(backtester)
    assert result["max_drawdown"] >= 0
    assert result["max_drawdown_money"] >= 0


def test_an_empty_history_raises_a_clear_error(config, database, eurusd_spec):
    empty = Backtester(config, market_data=FakeMarket(eurusd_spec, price_frame().head(0)), database=database)
    with pytest.raises(MT5Error, match="No historical candles"):
        run(empty)


def test_too_little_history_raises_a_clear_error(config, database, eurusd_spec):
    short = Backtester(config, market_data=FakeMarket(eurusd_spec, price_frame(bars=40)), database=database)
    with pytest.raises(MT5Error, match="candles available"):
        run(short)


def test_results_are_saved_when_requested(backtester, database):
    result = run(backtester, save=True)
    assert "id" in result
    stored = database.get_backtest(result["id"])
    assert stored is not None
    assert stored["total_trades"] == result["total_trades"]
    assert len(stored["equity_curve"]) == len(result["equity_curve"])


def test_the_same_inputs_give_the_same_result(backtester):
    """Determinism - a backtest with no randomness must reproduce exactly."""
    first, second = run(backtester), run(backtester)
    assert first["net_profit"] == second["net_profit"]
    assert first["total_trades"] == second["total_trades"]
