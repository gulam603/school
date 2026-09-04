"""Symbol mapping and the error-handling contract."""

from __future__ import annotations

import threading

import pytest

from backend.mt5_errors import (
    InsufficientMarginError, InvalidSymbolError, InvalidVolumeError,
    LiveTradingBlockedError, MarketClosedError, MT5ConnectionError, MT5Error,
    MT5NotAvailableError, OrderRejectedError,
)
from backend.order_manager import RETCODES, RETRYABLE_RETCODES, retcode_text
from backend.symbols import SymbolResolver, normalize


@pytest.fixture
def resolver():
    instance = SymbolResolver.__new__(SymbolResolver)
    instance._lock = threading.RLock()
    instance._forward = {}
    instance._reverse = {}
    instance._broker_symbols = []
    instance._overrides = {}
    return instance


# ----------------------------------------------------------------------
# symbol naming
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [("EURUSD", "EURUSD"), ("EURUSDm", "EURUSDM"), ("EURUSD.a", "EURUSDA"),
     ("eur/usd", "EURUSD"), ("FX_EURUSD", "FXEURUSD")],
)
def test_normalisation_strips_broker_decoration(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "broker_symbol,expected",
    [("EURUSD", "EURUSD"), ("EURUSDm", "EURUSD"), ("EURUSD.a", "EURUSD"),
     ("EURUSD_i", "EURUSD"), ("GBPUSD-ECN", "GBPUSD"), ("XAUUSDm", "XAUUSD"),
     ("GOLD", "XAUUSD"), ("SILVER", "XAGUSD")],
)
def test_broker_names_map_back_to_canonical_names(resolver, broker_symbol, expected):
    assert resolver.to_canonical(broker_symbol) == expected


def test_a_learned_mapping_is_reused(resolver):
    resolver._remember("EURUSD", "EURUSDm")
    assert resolver.to_canonical("EURUSDm") == "EURUSD"


# ----------------------------------------------------------------------
# errors
# ----------------------------------------------------------------------
def test_every_error_type_is_an_mt5_error():
    for error_type in (
        MT5NotAvailableError, MT5ConnectionError, InvalidSymbolError, MarketClosedError,
        InvalidVolumeError, InsufficientMarginError, OrderRejectedError, LiveTradingBlockedError,
    ):
        assert issubclass(error_type, MT5Error)


def test_errors_serialise_for_the_api():
    payload = InvalidVolumeError("volume too small", 10014).as_dict()
    assert payload == {"error": "InvalidVolumeError", "message": "volume too small", "code": 10014}


@pytest.mark.parametrize(
    "retcode,fragment",
    [(10004, "Requote"), (10018, "Market is closed"), (10019, "Not enough money"),
     (10016, "Invalid stops"), (10014, "Invalid volume"), (10027, "Autotrading disabled"),
     (10030, "filling mode"), (10031, "No connection")],
)
def test_every_documented_failure_has_a_readable_message(retcode, fragment):
    assert fragment.lower() in retcode_text(retcode).lower()


def test_transient_failures_are_marked_retryable():
    for retcode in (10004, 10020, 10021, 10024, 10012, 10031):
        assert retcode in RETRYABLE_RETCODES, f"{retcode} should be retryable"


def test_permanent_failures_are_not_retried():
    for retcode in (10014, 10016, 10018, 10019, 10027, 10036):
        assert retcode not in RETRYABLE_RETCODES, f"{retcode} must not be retried"


def test_an_unknown_retcode_still_produces_text():
    assert "99999" in retcode_text(99999)
    assert retcode_text(None)


def test_the_retcode_table_covers_the_documented_range():
    assert set(RETCODES) >= set(range(10004, 10036)) - {10005, 10037}
