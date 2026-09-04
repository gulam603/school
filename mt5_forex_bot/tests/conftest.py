"""Shared fixtures.

The tests never touch a real MetaTrader terminal: they exercise the pure
calculation layers (indicators, lot sizing, stop placement, the backtester and
the API surface) with deterministic inputs. Trade *execution* is only ever done
against a real MT5 demo terminal, which is why there is no fake order server
here - see tests/manual_mt5_checklist.md for the live checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import BotConfig  # noqa: E402
from backend.database import Database  # noqa: E402
from backend.market_data import SymbolSpec  # noqa: E402


@pytest.fixture
def eurusd_spec() -> SymbolSpec:
    """A typical 5-digit EURUSD contract: $1 per point on a 1.00 lot."""
    return SymbolSpec(
        name="EURUSD", canonical="EURUSD", digits=5, point=0.00001, tick_size=0.00001,
        tick_value=1.0, contract_size=100_000.0, volume_min=0.01, volume_max=100.0,
        volume_step=0.01, stops_level_points=0, freeze_level_points=0, spread_points=10,
        trade_mode=4, currency_profit="USD", currency_margin="EUR",
    )


@pytest.fixture
def gold_spec() -> SymbolSpec:
    """XAUUSD: 2 digits, 100 oz contract, a 10 point minimum stop distance."""
    return SymbolSpec(
        name="XAUUSD", canonical="XAUUSD", digits=2, point=0.01, tick_size=0.01,
        tick_value=1.0, contract_size=100.0, volume_min=0.01, volume_max=50.0,
        volume_step=0.01, stops_level_points=10, freeze_level_points=0, spread_points=25,
        trade_mode=4, currency_profit="USD", currency_margin="USD",
    )


@pytest.fixture
def micro_lot_spec() -> SymbolSpec:
    """A broker whose minimum volume is a whole lot - used for over-risk tests."""
    return SymbolSpec(
        name="EURUSD.big", canonical="EURUSD", digits=5, point=0.00001, tick_size=0.00001,
        tick_value=1.0, contract_size=100_000.0, volume_min=1.0, volume_max=100.0,
        volume_step=1.0, stops_level_points=0, freeze_level_points=0, spread_points=10,
        trade_mode=4, currency_profit="USD", currency_margin="EUR",
    )


@pytest.fixture
def config() -> BotConfig:
    cfg = BotConfig()
    cfg.validate()
    return cfg


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def trending_frame() -> pd.DataFrame:
    """Downtrend then uptrend - guarantees both EMA crossovers exist."""
    down = np.linspace(1.1000, 1.0500, 70)
    up = np.linspace(1.0500, 1.1500, 60)
    close = np.concatenate([down, up])
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=len(close), freq="h", tz="UTC"),
            "open": close,
            "high": close + 0.0010,
            "low": close - 0.0010,
            "close": close,
            "tick_volume": 100,
            "spread": 10,
            "real_volume": 0,
        }
    )
