"""Persistence: trades, signals, orders, logs, daily rows and backtests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def make_trade(ticket=1, **overrides):
    return {
        "ticket": ticket, "symbol": "EURUSD", "direction": "BUY", "volume": 0.10,
        "entry_price": 1.08400, "stop_loss": 1.08100, "take_profit": 1.09000,
        "initial_sl": 1.08100, "strategy": "ema_crossover", "timeframe": "H1",
        "opened_at": datetime.now(timezone.utc).isoformat(), "status": "open",
        "mode": "demo", **overrides,
    }


def test_every_required_table_exists(database):
    names = {
        row["name"]
        for row in database.query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "app_config", "trades", "positions", "orders", "signals",
        "strategy_runs", "bot_logs", "daily_performance", "backtest_results",
    } <= names


def test_a_trade_round_trips(database):
    database.insert_trade(make_trade())
    stored = database.get_trade(1)
    assert stored["symbol"] == "EURUSD"
    assert stored["status"] == "open"
    assert stored["strategy"] == "ema_crossover"


def test_closing_a_trade_records_the_outcome(database):
    database.insert_trade(make_trade())
    database.close_trade(1, exit_price=1.09000, profit=60.0, close_reason="TP")
    stored = database.get_trade(1)
    assert stored["status"] == "closed"
    assert stored["profit"] == 60.0
    assert stored["close_reason"] == "TP"
    assert stored["closed_at"] is not None


def test_statistics_reflect_wins_and_losses(database):
    database.insert_trade(make_trade(1))
    database.insert_trade(make_trade(2))
    database.insert_trade(make_trade(3))
    database.close_trade(1, 1.09, 60.0, "TP")
    database.close_trade(2, 1.081, -30.0, "SL")
    database.close_trade(3, 1.09, 40.0, "TP")
    stats = database.trade_statistics()
    assert stats["total_trades"] == 3
    assert stats["winning_trades"] == 2
    assert stats["losing_trades"] == 1
    assert stats["win_rate"] == pytest.approx(66.67, abs=0.01)
    assert stats["net_profit"] == pytest.approx(70.0)


def test_open_trades_only_returns_open_ones(database):
    database.insert_trade(make_trade(1))
    database.insert_trade(make_trade(2))
    database.close_trade(2, 1.09, 10.0, "TP")
    assert [trade["ticket"] for trade in database.open_trades()] == [1]


def test_updating_levels_and_volume(database):
    database.insert_trade(make_trade())
    database.update_trade_levels(1, 1.08500, 1.09500)
    database.update_trade_volume(1, 0.05)
    stored = database.get_trade(1)
    assert stored["stop_loss"] == 1.08500
    assert stored["volume"] == 0.05


def test_signals_record_their_rejection_reason(database):
    signal_id = database.insert_signal({
        "symbol": "EURUSD", "timeframe": "H1", "strategy": "ema_crossover",
        "direction": "BUY", "price": 1.084, "reason": "cross up", "indicators": {"rsi": 55},
    })
    database.mark_signal(signal_id, False, "Daily loss limit reached")
    stored = database.recent_signals()[0]
    assert stored["executed"] == 0
    assert stored["rejected_reason"] == "Daily loss limit reached"


def test_orders_store_the_request_and_the_reply(database):
    database.insert_order({
        "symbol": "EURUSD", "action": "OPEN", "order_type": "BUY", "volume": 0.1,
        "price": 1.084, "retcode": 10009, "retcode_text": "Request completed",
        "request": {"volume": 0.1}, "result": {"retcode": 10009}, "success": True, "mode": "demo",
    })
    stored = database.recent_orders()[0]
    assert stored["success"] == 1
    assert '"volume": 0.1' in stored["request_json"]


def test_positions_snapshot_is_replaced_not_appended(database):
    for ticket in (1, 2, 3):
        database.upsert_position({
            "ticket": ticket, "symbol": "EURUSD", "direction": "BUY", "volume": 0.1,
            "entry_price": 1.08, "current_price": 1.081, "stop_loss": 1.07,
            "take_profit": 1.09, "profit": 1.0, "swap": 0.0, "opened_at": "now", "magic": 1,
        })
    database.delete_positions_except([1])
    assert len(database.query("SELECT * FROM positions")) == 1


def test_position_flags_persist_break_even_state(database):
    database.upsert_position({
        "ticket": 5, "symbol": "EURUSD", "direction": "BUY", "volume": 0.1,
        "entry_price": 1.08, "current_price": 1.081, "stop_loss": 1.07,
        "take_profit": 1.09, "profit": 1.0, "swap": 0.0, "opened_at": "now", "magic": 1,
    })
    database.set_position_flag(5, "break_even_done", True)
    assert database.get_position(5)["break_even_done"] == 1
    with pytest.raises(ValueError):
        database.set_position_flag(5, "drop table", True)


def test_config_round_trips_as_json(database):
    database.save_config("bot_config", {"risk_per_trade_percent": 1.5, "symbols": ["EURUSD"]})
    assert database.load_config("bot_config")["risk_per_trade_percent"] == 1.5
    assert database.load_config("missing") is None


def test_logs_can_be_filtered_by_category(database):
    database.insert_log({"timestamp": "2024-01-01T00:00:00Z", "category": "TRADE", "message": "opened"})
    database.insert_log({"timestamp": "2024-01-01T00:00:01Z", "category": "ERROR", "message": "boom"})
    assert len(database.recent_logs(category="ERROR")) == 1
    assert len(database.recent_logs()) == 2


def test_strategy_runs_track_signal_and_trade_counts(database):
    run_id = database.start_strategy_run({
        "strategy": "ema_crossover", "symbols": ["EURUSD"], "timeframe": "H1", "mode": "demo",
    })
    database.stop_strategy_run(run_id, "manual", signals=4, trades=2)
    stored = database.recent_strategy_runs()[0]
    assert stored["signals_count"] == 4
    assert stored["stop_reason"] == "manual"


def test_backtests_are_stored_with_their_curve_and_trades(database):
    backtest_id = database.insert_backtest({
        "symbol": "EURUSD", "timeframe": "H1", "strategy": "ema_crossover",
        "initial_balance": 10_000, "final_balance": 10_500, "total_trades": 5,
        "equity_curve": [{"time": "t", "equity": 10_000}], "trades": [{"ticket": 1}],
        "params": {"ema_fast": 20},
    })
    stored = database.get_backtest(backtest_id)
    assert stored["equity_curve"][0]["equity"] == 10_000
    assert stored["params"]["ema_fast"] == 20
    database.delete_backtest(backtest_id)
    assert database.get_backtest(backtest_id) is None
