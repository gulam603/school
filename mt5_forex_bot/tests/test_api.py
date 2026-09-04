"""API surface: routes, safety of the payloads and graceful degradation.

These run against the real FastAPI app with no MT5 terminal available, which is
exactly the state the dashboard must survive: connection-dependent endpoints
answer 503 with a typed error, everything else keeps working.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_demo_mode(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["demo_mode"] is True


def test_the_mode_endpoint_drives_the_demo_banner(client):
    body = client.get("/api/mode").json()
    assert body["demo"] is True
    assert body["label"] == "DEMO"
    assert "DEMO MODE" in body["warning"]


def test_status_snapshot_is_complete_even_without_mt5(client):
    body = client.get("/api/status").json()
    for key in ("connection", "account", "mode", "bot", "risk", "positions", "statistics"):
        assert key in body
    assert body["account"] is None
    assert body["mode"]["demo"] is True
    assert body["risk"]["max_open_trades"] >= 1


def test_account_returns_503_when_disconnected(client):
    response = client.get("/api/account")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "MT5NotConnectedError"


def test_market_watch_degrades_per_symbol(client):
    body = client.get("/api/market/watch").json()
    assert body["symbols"]
    assert all(row["error"] for row in body["symbols"])   # not connected, but no crash


def test_settings_never_leak_credentials(client):
    body = client.get("/api/settings").json()
    serialised = str(body)
    assert "password" not in serialised.lower()
    assert "token" not in serialised.lower()
    assert "config" in body and "environment" in body


def test_settings_can_be_updated_and_are_validated(client):
    original = client.get("/api/settings").json()["config"]["risk_per_trade_percent"]
    try:
        response = client.put("/api/settings", json={"risk_per_trade_percent": 2.5})
        assert response.status_code == 200
        assert "risk_per_trade_percent" in response.json()["applied"]
        assert client.get("/api/settings").json()["config"]["risk_per_trade_percent"] == 2.5
    finally:
        client.put("/api/settings", json={"risk_per_trade_percent": original})


def test_an_out_of_range_setting_is_rejected(client):
    assert client.put("/api/settings", json={"risk_per_trade_percent": 99}).status_code == 422


def test_a_contradictory_setting_is_rejected_with_a_reason(client):
    response = client.put("/api/settings", json={"ema_fast": 100, "ema_slow": 50})
    assert response.status_code == 422
    assert "EMA fast" in response.json()["detail"]


def test_strategies_are_listed_with_their_parameters(client):
    body = client.get("/api/strategies").json()
    assert any(item["name"] == "ema_crossover" for item in body["strategies"])
    assert body["active"] == "ema_crossover"


def test_the_bot_cannot_start_without_a_terminal(client):
    response = client.post("/api/bot/start")
    assert response.status_code == 503
    assert client.get("/api/bot/status").json()["running"] is False


def test_stopping_an_idle_bot_is_harmless(client):
    assert client.post("/api/bot/stop").status_code == 200


def test_risk_endpoint_exposes_the_limits(client):
    body = client.get("/api/risk").json()
    assert body["max_daily_loss_percent"] > 0
    assert body["min_risk_reward"] > 0


def test_logs_are_queryable_by_category(client):
    body = client.get("/api/logs?limit=5&category=INFO").json()
    assert "logs" in body
    assert "INFO" in body["categories"]


def test_backtest_rejects_an_inverted_date_range(client):
    response = client.post("/api/backtest/run", json={
        "symbol": "EURUSD", "timeframe": "H1",
        "start_date": "2024-06-01", "end_date": "2024-01-01",
        "initial_balance": 10000, "risk_percent": 1,
    })
    assert response.status_code == 422


def test_an_unknown_backtest_id_is_a_404(client):
    assert client.get("/api/backtest/results/999999").status_code == 404


def test_trade_history_is_available_offline(client):
    body = client.get("/api/history/trades?limit=5").json()
    assert "trades" in body and "statistics" in body


def test_the_websocket_sends_an_immediate_snapshot(client):
    with client.websocket_connect("/ws") as websocket:
        message = websocket.receive_json()
        assert message["type"] == "snapshot"
        assert "bot" in message["payload"]
