"""Shared application state.

One instance holds everything the API routers and the WebSocket broadcaster
need: the runtime configuration, the MT5 connection, the strategy engine and
the database.  It also owns the dashboard *snapshot* - a single payload with
account, bot, risk and position state that both REST and WebSocket serve, so
the two can never disagree.
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone
from typing import Any

from backend.backtester import Backtester
from backend.config import BotConfig, settings
from backend.database import get_db
from backend.logger import get_logger, register_sink
from backend.market_data import get_market_data
from backend.mt5_connection import get_connection
from backend.mt5_errors import MT5Error
from backend.notifications import get_notifier
from backend.strategy_engine import StrategyEngine
from backend.symbols import get_resolver

log = get_logger("app")

CONFIG_KEY = "bot_config"


class AppState:
    """Singleton wiring the whole backend together."""

    def __init__(self) -> None:
        self.db = get_db()
        self.config = self._load_config()
        self.conn = get_connection()
        self.market = get_market_data()
        self.resolver = get_resolver()
        self.notifier = get_notifier()
        self.engine = StrategyEngine(self.config, self.conn, self.market, self.db, self.notifier)
        self.backtester = Backtester(self.config, self.market, self.db)

        self._lock = threading.RLock()
        #: Events produced by background threads, drained by the WS broadcaster.
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
        self.started_at = datetime.now(timezone.utc)

        self.notifier.set_enabled(self.config.telegram_notifications)
        self.engine.add_listener(self._on_engine_event)
        self.conn.on_state_change(self._on_connection_change)
        register_sink(self._on_log_record)

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    def _load_config(self) -> BotConfig:
        config = BotConfig.from_env()
        stored = self.db.load_config(CONFIG_KEY)
        if isinstance(stored, dict):
            try:
                config.update(stored)
                log.info("Loaded saved configuration from the database")
            except ValueError as exc:
                log.warning(f"Saved configuration rejected ({exc}), falling back to .env defaults")
                config = BotConfig.from_env()
        config.validate()
        return config

    def update_config(self, changes: dict[str, Any]) -> list[str]:
        """Validate, apply and persist a partial configuration change."""
        with self._lock:
            applied = self.config.update(changes)
            self.db.save_config(CONFIG_KEY, self.config.to_dict())
            if "telegram_notifications" in applied:
                self.notifier.set_enabled(self.config.telegram_notifications)
            if applied:
                log.info(f"Settings updated: {', '.join(applied)}")
                self.market.invalidate()
                self.publish("config", self.config.to_dict())
            return applied

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def publish(self, event: str, payload: Any) -> None:
        """Queue an event for every connected dashboard."""
        try:
            self.events.put_nowait({"type": event, "payload": payload})
        except queue.Full:
            pass  # a slow dashboard must never block trading

    def _on_engine_event(self, event: str, payload: dict[str, Any]) -> None:
        self.publish(event, payload)

    def _on_log_record(self, record: dict[str, Any]) -> None:
        try:
            self.db.insert_log(record)
        except Exception:
            pass  # logging must never raise into the caller
        self.publish("log", record)

    def _on_connection_change(self, connected: bool, reason: str) -> None:
        self.publish("connection", {"connected": connected, "reason": reason})
        if connected:
            self.notifier.reconnected(self.conn.status().get("account_number"))
        else:
            self.notifier.disconnected(reason)

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------
    def account(self) -> dict[str, Any] | None:
        try:
            return self.conn.account_info()
        except MT5Error:
            return None

    def snapshot(self) -> dict[str, Any]:
        """Everything the dashboard needs in one payload. Never raises."""
        account = self.account()
        connection = self.conn.status()
        positions: list[dict[str, Any]] = []

        if account is not None:
            try:
                positions = self.engine.positions.list_positions()
            except MT5Error as exc:
                log.debug(f"snapshot positions failed: {exc.message}")

        # The risk summary is always present, even while disconnected: the
        # configured limits are what the dashboard panels render, and only the
        # live "daily" section depends on the terminal.
        risk = self.engine.risk.summary(
            open_positions=len([p for p in positions if p["is_bot_position"]])
        )
        daily = risk.get("daily") or {}
        today_pnl = float(daily.get("total_pnl", 0.0) or 0.0)

        stats = self.db.trade_statistics()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connection": connection,
            "account": account,
            "mode": {
                "demo": settings.is_demo_mode,
                "label": "DEMO" if settings.is_demo_mode else "LIVE",
                "live_unlocked": settings.live_trading_unlocked,
                "account_is_demo": connection.get("is_demo_account"),
            },
            "bot": self.engine.status(),
            "risk": risk,
            "positions": positions,
            "open_positions": len(positions),
            "today_pnl": round(today_pnl, 2),
            "statistics": stats,
            "recent_trades": self.db.recent_trades(limit=25),
            "server_uptime_seconds": int(
                (datetime.now(timezone.utc) - self.started_at).total_seconds()
            ),
        }

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if self.engine.running:
            self.engine.stop("server_shutdown")
        self.notifier.stop()
        self.conn.disconnect("server_shutdown")


_state: AppState | None = None
_state_lock = threading.Lock()


def get_state() -> AppState:
    global _state
    if _state is None:
        with _state_lock:
            if _state is None:
                _state = AppState()
    return _state
