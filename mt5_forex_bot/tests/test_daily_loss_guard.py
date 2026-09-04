"""The daily loss limit: the rule that stops a bad day becoming a bad month."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.config import BotConfig
from backend.database import today_key
from backend.risk_manager import RiskManager


class FakeConnection:
    """Stands in for the MT5 terminal with a scriptable account state."""

    def __init__(self, balance=10_000.0, equity=10_000.0, floating=0.0):
        self.account = {
            "login": 123, "balance": balance, "equity": equity, "profit": floating,
            "free_margin": equity, "currency": "USD", "is_demo": True, "trade_allowed": True,
        }
        self.deals: list[object] = []

    def account_info(self):
        return dict(self.account)

    def history_deals_get(self, *_args, **_kwargs):
        return tuple(self.deals)


class FakeDeal:
    def __init__(self, profit, entry=1, deal_type=0, commission=0.0, swap=0.0):
        self.profit = profit
        self.entry = entry
        self.type = deal_type
        self.commission = commission
        self.swap = swap


@pytest.fixture
def risk(config: BotConfig, database):
    config.max_daily_loss_percent = 5.0
    return lambda connection: RiskManager(config, database, market_data=None, connection=connection)


def test_a_fresh_day_starts_with_no_loss(risk):
    status = risk(FakeConnection()).daily_status()
    assert status.loss_percent == 0.0
    assert status.limit_hit is False


def test_realized_losses_count_towards_the_limit(risk):
    connection = FakeConnection(balance=9_600.0, equity=9_600.0)
    connection.deals = [FakeDeal(-200.0), FakeDeal(-200.0)]
    status = risk(connection).daily_status()
    assert status.realized_pnl == pytest.approx(-400.0)
    assert status.starting_balance == pytest.approx(10_000.0)
    assert status.loss_percent == pytest.approx(4.0)
    assert status.limit_hit is False


def test_the_limit_trips_exactly_at_the_configured_percentage(risk):
    connection = FakeConnection(balance=9_500.0, equity=9_500.0)
    connection.deals = [FakeDeal(-500.0)]
    status = risk(connection).daily_status()
    assert status.loss_percent == pytest.approx(5.0)
    assert status.limit_hit is True


def test_floating_losses_also_count(risk):
    """An open position deep in the red must trip the guard too."""
    connection = FakeConnection(balance=10_000.0, equity=9_400.0, floating=-600.0)
    status = risk(connection).daily_status()
    assert status.floating_pnl == pytest.approx(-600.0)
    assert status.loss_percent == pytest.approx(6.0)
    assert status.limit_hit is True


def test_profit_never_produces_a_negative_loss_percentage(risk):
    connection = FakeConnection(balance=10_500.0, equity=10_500.0)
    connection.deals = [FakeDeal(500.0)]
    status = risk(connection).daily_status()
    assert status.loss_percent == 0.0
    assert status.limit_hit is False


def test_the_days_starting_balance_survives_a_restart(risk, database):
    """A restart must not hand the bot a fresh loss budget."""
    connection = FakeConnection(balance=9_700.0, equity=9_700.0)
    connection.deals = [FakeDeal(-300.0)]
    first = risk(connection).daily_status()
    assert first.starting_balance == pytest.approx(10_000.0)

    # "restart": a brand new RiskManager reading the same database
    reborn = risk(connection).daily_status()
    assert reborn.starting_balance == pytest.approx(10_000.0)
    assert reborn.loss_percent == pytest.approx(3.0)


def test_the_daily_row_is_persisted_for_the_performance_page(risk, database):
    connection = FakeConnection(balance=9_800.0, equity=9_800.0)
    connection.deals = [FakeDeal(-200.0)]
    risk(connection).daily_status()
    row = database.get_day(today_key())
    assert row is not None
    assert row["realized_pnl"] == pytest.approx(-200.0)
    assert row["daily_loss_hit"] == 0


def test_only_closing_deals_are_counted_as_trades(risk):
    connection = FakeConnection(balance=9_900.0, equity=9_900.0)
    connection.deals = [
        FakeDeal(0.0, entry=0),      # DEAL_ENTRY_IN - opening, not a result
        FakeDeal(-100.0, entry=1),   # DEAL_ENTRY_OUT - a closed trade
    ]
    status = risk(connection).daily_status()
    assert status.trades_today == 1
    assert status.losses_today == 1


def test_balance_operations_are_ignored(risk):
    """Deposits/withdrawals (deal type > 1) must not look like trading P/L."""
    connection = FakeConnection(balance=15_000.0, equity=15_000.0)
    connection.deals = [FakeDeal(5_000.0, entry=0, deal_type=2)]
    status = risk(connection).daily_status()
    assert status.realized_pnl == 0.0


def test_commission_and_swap_are_part_of_the_result(risk):
    connection = FakeConnection(balance=9_880.0, equity=9_880.0)
    connection.deals = [FakeDeal(-100.0, commission=-15.0, swap=-5.0)]
    status = risk(connection).daily_status()
    assert status.realized_pnl == pytest.approx(-120.0)


def test_daily_limit_hit_helper_matches_the_status(risk):
    connection = FakeConnection(balance=9_000.0, equity=9_000.0)
    connection.deals = [FakeDeal(-1_000.0)]
    manager = risk(connection)
    assert manager.daily_limit_hit() is True


def test_an_unreadable_account_fails_closed(risk, config, database):
    """If the state cannot be read, the answer is 'no new trades'."""
    from backend.mt5_errors import MT5NotConnectedError

    class DeadConnection(FakeConnection):
        def account_info(self):
            raise MT5NotConnectedError("terminal gone")

    manager = RiskManager(config, database, market_data=None, connection=DeadConnection())
    assert manager.daily_limit_hit() is True
