"""Position management rules: R multiples, break-even and trailing stops."""

from __future__ import annotations

import pytest

from backend.position_manager import PositionManager


@pytest.fixture
def manager():
    return PositionManager.__new__(PositionManager)   # pure helpers, no MT5 needed


def buy(entry=1.0840, sl=1.0810, current=1.0840, tp=1.0900):
    return {
        "direction": "BUY", "entry_price": entry, "initial_sl": sl,
        "stop_loss": sl, "take_profit": tp, "current_price": current,
    }


def sell(entry=1.0840, sl=1.0870, current=1.0840, tp=1.0780):
    return {
        "direction": "SELL", "entry_price": entry, "initial_sl": sl,
        "stop_loss": sl, "take_profit": tp, "current_price": current,
    }


# ----------------------------------------------------------------------
# R multiple
# ----------------------------------------------------------------------
def test_a_buy_at_its_stop_is_minus_one_r(manager):
    assert manager._r_multiple(buy(current=1.0810)) == pytest.approx(-1.0)


def test_a_buy_one_risk_unit_up_is_plus_one_r(manager):
    assert manager._r_multiple(buy(current=1.0870)) == pytest.approx(1.0)


def test_a_sell_one_risk_unit_down_is_plus_one_r(manager):
    assert manager._r_multiple(sell(current=1.0810)) == pytest.approx(1.0)


def test_a_sell_at_its_stop_is_minus_one_r(manager):
    assert manager._r_multiple(sell(current=1.0870)) == pytest.approx(-1.0)


def test_r_multiple_is_none_without_a_stop(manager):
    position = buy()
    position["initial_sl"] = None
    position["stop_loss"] = None
    assert manager._r_multiple(position) is None


def test_r_multiple_is_none_when_entry_equals_the_stop(manager):
    assert manager._r_multiple(buy(sl=1.0840)) is None


# ----------------------------------------------------------------------
# stop movement
# ----------------------------------------------------------------------
def test_a_buy_stop_may_only_move_up(manager):
    position = buy(current=1.0900)
    assert manager._improves_stop(position, 1.0850, 0.00001) is True
    assert manager._improves_stop(position, 1.0800, 0.00001) is False


def test_a_sell_stop_may_only_move_down(manager):
    position = sell(current=1.0800)
    assert manager._improves_stop(position, 1.0850, 0.00001) is True
    assert manager._improves_stop(position, 1.0900, 0.00001) is False


def test_a_buy_stop_is_never_placed_above_the_current_price(manager):
    position = buy(current=1.0900)
    assert manager._improves_stop(position, 1.0905, 0.00001) is False


def test_a_sell_stop_is_never_placed_below_the_current_price(manager):
    position = sell(current=1.0800)
    assert manager._improves_stop(position, 1.0795, 0.00001) is False


def test_a_stop_that_barely_moves_is_not_worth_an_order(manager):
    position = buy(current=1.0900, sl=1.0850)
    assert manager._improves_stop(position, 1.08500, 0.00001) is False


# ----------------------------------------------------------------------
# close classification
# ----------------------------------------------------------------------
def test_a_close_near_the_target_is_recorded_as_tp(manager):
    trade = {"stop_loss": 1.0810, "take_profit": 1.0900}
    assert manager._close_reason(trade, 1.09001, "") == "TP"


def test_a_close_near_the_stop_is_recorded_as_sl(manager):
    trade = {"stop_loss": 1.0810, "take_profit": 1.0900}
    assert manager._close_reason(trade, 1.08099, "") == "SL"


def test_the_brokers_own_comment_wins(manager):
    trade = {"stop_loss": 1.0810, "take_profit": 1.0900}
    assert manager._close_reason(trade, 1.0850, "[sl 1.0810]") == "SL"
    assert manager._close_reason(trade, 1.0850, "[tp 1.0900]") == "TP"


def test_an_unknown_close_is_not_guessed(manager):
    assert manager._close_reason({}, None, "") == "closed"
