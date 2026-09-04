"""Risk management - the tests that matter most.

Everything here guards money: position sizing, stop placement, the minimum
risk/reward, the daily-loss guard and the explicit absence of martingale.
"""

from __future__ import annotations

import pytest

from backend.risk_manager import (
    apply_min_stop_distance, atr_stop_distance, calculate_lot_size, loss_per_lot,
    risk_reward_ratio, stop_loss_price, take_profit_price,
)


# ----------------------------------------------------------------------
# stop distance
# ----------------------------------------------------------------------
def test_stop_distance_is_atr_times_the_multiplier():
    assert atr_stop_distance(0.0020, 1.5) == pytest.approx(0.0030)


@pytest.mark.parametrize("bad_atr", [0, -1, None])
def test_stop_distance_refuses_a_non_positive_atr(bad_atr):
    with pytest.raises(ValueError):
        atr_stop_distance(bad_atr, 1.5)


def test_stop_distance_is_widened_to_the_brokers_minimum(gold_spec):
    # gold has a 10 point (0.10) minimum stop level
    assert apply_min_stop_distance(0.02, gold_spec) == pytest.approx(0.11)


def test_stop_distance_larger_than_the_minimum_is_left_alone(gold_spec):
    assert apply_min_stop_distance(5.25, gold_spec) == pytest.approx(5.25)


# ----------------------------------------------------------------------
# stop / target placement
# ----------------------------------------------------------------------
def test_buy_stop_is_below_and_target_above_entry(eurusd_spec):
    entry, distance = 1.08400, 0.0030
    assert stop_loss_price(entry, "BUY", distance, eurusd_spec) == pytest.approx(1.08100)
    assert take_profit_price(entry, "BUY", distance, 2.0, eurusd_spec) == pytest.approx(1.09000)


def test_sell_stop_is_above_and_target_below_entry(eurusd_spec):
    entry, distance = 1.08400, 0.0030
    assert stop_loss_price(entry, "SELL", distance, eurusd_spec) == pytest.approx(1.08700)
    assert take_profit_price(entry, "SELL", distance, 2.0, eurusd_spec) == pytest.approx(1.07800)


def test_prices_are_rounded_to_the_symbols_digits(gold_spec):
    assert stop_loss_price(2345.678, "BUY", 5.0, gold_spec) == pytest.approx(2340.68)


@pytest.mark.parametrize("direction,rr", [("BUY", 2.0), ("SELL", 2.0), ("BUY", 3.0), ("SELL", 1.5)])
def test_take_profit_always_delivers_the_requested_risk_reward(direction, rr, eurusd_spec):
    entry, distance = 1.10000, 0.0025
    stop = stop_loss_price(entry, direction, distance, eurusd_spec)
    target = take_profit_price(entry, direction, distance, rr, eurusd_spec)
    assert risk_reward_ratio(entry, stop, target) == pytest.approx(rr, abs=0.01)


def test_risk_reward_of_a_zero_risk_trade_is_zero():
    assert risk_reward_ratio(1.1, 1.1, 1.2) == 0.0


# ----------------------------------------------------------------------
# lot sizing
# ----------------------------------------------------------------------
def test_loss_per_lot_uses_the_tick_value(eurusd_spec):
    # 0.0030 / 0.00001 = 300 ticks, $1 each on a 1.00 lot
    assert loss_per_lot(eurusd_spec, 0.0030) == pytest.approx(300.0)


def test_loss_per_lot_falls_back_to_contract_size_when_tick_value_is_missing(eurusd_spec):
    spec = type(eurusd_spec)(**{**eurusd_spec.__dict__, "tick_value": 0.0})
    assert loss_per_lot(spec, 0.0030) == pytest.approx(0.0030 * 100_000)


def test_lot_size_risks_the_configured_percentage(eurusd_spec):
    result = calculate_lot_size(10_000, 1.0, 0.0030, eurusd_spec, max_lot=10.0)
    # $100 budget / $300 per lot = 0.333 -> 0.33 after the 0.01 step
    assert result["volume"] == pytest.approx(0.33)
    assert result["actual_risk_amount"] <= result["risk_amount"]
    assert result["actual_risk_percent"] <= 1.0


def test_lot_size_scales_linearly_with_the_balance(eurusd_spec):
    small = calculate_lot_size(10_000, 1.0, 0.0030, eurusd_spec, max_lot=100.0)
    large = calculate_lot_size(20_000, 1.0, 0.0030, eurusd_spec, max_lot=100.0)
    assert large["volume"] == pytest.approx(small["volume"] * 2, abs=0.01)


def test_lot_size_shrinks_when_the_stop_is_wider(eurusd_spec):
    tight = calculate_lot_size(10_000, 1.0, 0.0015, eurusd_spec, max_lot=100.0)
    wide = calculate_lot_size(10_000, 1.0, 0.0060, eurusd_spec, max_lot=100.0)
    assert wide["volume"] < tight["volume"]
    assert wide["actual_risk_amount"] <= 100.0
    assert tight["actual_risk_amount"] <= 100.0


def test_lot_size_never_rounds_up_past_the_risk_budget(eurusd_spec):
    # 0.3333 lots would be needed; rounding up to 0.34 would risk 1.02%
    result = calculate_lot_size(10_000, 1.0, 0.0030, eurusd_spec, max_lot=10.0)
    assert result["volume"] == 0.33
    assert result["actual_risk_percent"] < 1.0


def test_lot_size_respects_the_configured_maximum(eurusd_spec):
    result = calculate_lot_size(1_000_000, 1.0, 0.0030, eurusd_spec, max_lot=1.0)
    assert result["volume"] == 1.0
    assert result["capped_by_max_lot"] is True


def test_lot_size_respects_the_brokers_volume_maximum(eurusd_spec):
    result = calculate_lot_size(100_000_000, 1.0, 0.0030, eurusd_spec, max_lot=500.0)
    assert result["volume"] <= eurusd_spec.volume_max


def test_lot_size_is_snapped_to_the_brokers_volume_step(gold_spec):
    result = calculate_lot_size(10_000, 1.0, 5.25, gold_spec, max_lot=10.0)
    assert round(result["volume"] / gold_spec.volume_step) * gold_spec.volume_step == pytest.approx(result["volume"])


def test_a_minimum_lot_that_over_risks_is_flagged_and_must_be_refused(micro_lot_spec):
    """A 1.00 minimum lot on a small account risks far more than 1%."""
    result = calculate_lot_size(1_000, 1.0, 0.0030, micro_lot_spec, max_lot=10.0)
    assert result["volume"] == 1.0
    assert result["actual_risk_amount"] == pytest.approx(300.0)
    assert result["within_risk"] is False   # the engine refuses the trade on this flag
    assert result["below_min_lot"] is True


def test_a_minimum_lot_that_fits_the_budget_is_allowed(micro_lot_spec):
    result = calculate_lot_size(100_000, 1.0, 0.0030, micro_lot_spec, max_lot=10.0)
    assert result["within_risk"] is True


def test_zero_balance_is_rejected(eurusd_spec):
    with pytest.raises(ValueError):
        calculate_lot_size(0, 1.0, 0.0030, eurusd_spec, max_lot=1.0)


def test_zero_stop_distance_is_rejected(eurusd_spec):
    with pytest.raises(ValueError):
        calculate_lot_size(10_000, 1.0, 0.0, eurusd_spec, max_lot=1.0)


# ----------------------------------------------------------------------
# no martingale / no averaging up after losses
# ----------------------------------------------------------------------
def test_position_size_is_a_pure_function_of_balance_risk_and_stop(eurusd_spec):
    """Identical inputs must always give an identical size.

    Nothing about previous trades can enter the calculation - this is what
    makes martingale structurally impossible in this bot.
    """
    first = calculate_lot_size(10_000, 1.0, 0.0030, eurusd_spec, max_lot=10.0)
    for _ in range(50):
        assert calculate_lot_size(10_000, 1.0, 0.0030, eurusd_spec, max_lot=10.0) == first


def test_size_falls_with_the_balance_after_a_loss(eurusd_spec):
    """After a losing trade the balance is smaller, so the next size is smaller."""
    before = calculate_lot_size(10_000, 1.0, 0.0030, eurusd_spec, max_lot=10.0)
    after_loss = calculate_lot_size(9_900, 1.0, 0.0030, eurusd_spec, max_lot=10.0)
    assert after_loss["volume"] <= before["volume"]
