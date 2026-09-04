"""Symbol specification maths: volume snapping and price rounding."""

from __future__ import annotations

import pytest

from backend.market_data import SymbolSpec


def test_volume_is_snapped_to_the_broker_step(eurusd_spec):
    assert eurusd_spec.normalize_volume(0.333) == pytest.approx(0.33)
    assert eurusd_spec.normalize_volume(0.336) == pytest.approx(0.34)


def test_volume_is_clamped_to_the_broker_bounds(eurusd_spec):
    assert eurusd_spec.normalize_volume(0.0001) == pytest.approx(eurusd_spec.volume_min)
    assert eurusd_spec.normalize_volume(9_999) == pytest.approx(eurusd_spec.volume_max)


def test_prices_are_rounded_to_the_symbols_digits(eurusd_spec, gold_spec):
    assert eurusd_spec.normalize_price(1.084567891) == 1.08457
    assert gold_spec.normalize_price(2345.6789) == 2345.68


def test_a_pip_is_ten_points_on_a_five_digit_quote(eurusd_spec):
    assert eurusd_spec.pip_size == pytest.approx(0.0001)


def test_a_pip_equals_a_point_on_a_two_digit_quote(gold_spec):
    assert gold_spec.pip_size == pytest.approx(0.01)


def test_only_fully_tradable_symbols_are_tradable(eurusd_spec):
    assert eurusd_spec.tradable is True
    close_only = type(eurusd_spec)(**{**eurusd_spec.__dict__, "trade_mode": 3})
    assert close_only.tradable is False


def test_the_spec_serialises_for_the_api(eurusd_spec):
    payload = eurusd_spec.to_dict()
    assert payload["volume_step"] == 0.01
    assert payload["digits"] == 5
    assert payload["tradable"] is True
