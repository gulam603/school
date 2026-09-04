"""Optional Telegram notifications.

Messages are queued and delivered by a background worker thread, so a slow or
unreachable Telegram API can never delay order execution.  Credentials are read
from the environment only (``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``); they
are never sent to the frontend and never written to the database.
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Any

import httpx

from backend.config import settings
from backend.logger import get_logger

log = get_logger("notifications")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

#: Every event the bot can announce, with the icon used in the message.
EVENTS = {
    "bot_started": "*",
    "bot_stopped": "*",
    "signal_buy": "^",
    "signal_sell": "v",
    "trade_opened": "+",
    "trade_closed": "-",
    "sl_hit": "!",
    "tp_hit": "$",
    "daily_loss_limit": "!!",
    "disconnected": "x",
    "reconnected": "o",
    "critical_error": "!!!",
}


class Notifier:
    """Fire-and-forget notification sink."""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=200)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._enabled = bool(
            settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_chat_id
        )
        self._runtime_enabled = self._enabled
        self._last_error: str = ""
        self._sent = 0

    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(settings.telegram_bot_token and settings.telegram_chat_id)

    @property
    def enabled(self) -> bool:
        return self.configured and self._runtime_enabled

    def set_enabled(self, enabled: bool) -> None:
        """Toggle notifications at runtime (Settings page switch)."""
        self._runtime_enabled = bool(enabled)
        if self._runtime_enabled and not self.configured:
            log.warning(
                "Telegram notifications requested but TELEGRAM_BOT_TOKEN / "
                "TELEGRAM_CHAT_ID are not set in .env"
            )

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "messages_sent": self._sent,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="notifier", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(("", ""))  # wake the worker
        except queue.Full:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event, text = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if not text:
                continue
            self._deliver(event, text)

    def _deliver(self, event: str, text: str) -> None:
        try:
            response = httpx.post(
                TELEGRAM_API.format(token=settings.telegram_bot_token),
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10.0,
            )
            if response.status_code != 200:
                self._last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                log.warning(f"Telegram delivery failed ({event}): {self._last_error}")
            else:
                self._sent += 1
                self._last_error = ""
        except Exception as exc:  # never let a notification break the bot
            self._last_error = str(exc)
            log.warning(f"Telegram delivery error ({event}): {exc}")

    # ------------------------------------------------------------------
    def send(self, event: str, message: str, **fields: Any) -> None:
        """Queue a notification. Silently ignored when disabled."""
        if not self.enabled:
            return
        icon = EVENTS.get(event, "*")
        stamp = datetime.now().strftime("%H:%M:%S")
        mode = "DEMO" if settings.is_demo_mode else "LIVE"
        lines = [f"`[{stamp}]` *{icon} {message}*", f"_mode: {mode}_"]
        for key, value in fields.items():
            if value is None:
                continue
            lines.append(f"{key.replace('_', ' ').title()}: `{value}`")
        text = "\n".join(lines)

        self.start()
        try:
            self._queue.put_nowait((event, text))
        except queue.Full:
            log.debug("Notification queue full, dropping message")

    # -- convenience wrappers -----------------------------------------
    def bot_started(self, strategy: str, symbols: list[str], timeframe: str) -> None:
        self.send("bot_started", "Bot started", strategy=strategy,
                  symbols=", ".join(symbols), timeframe=timeframe)

    def bot_stopped(self, reason: str) -> None:
        self.send("bot_stopped", "Bot stopped", reason=reason)

    def signal(self, direction: str, symbol: str, price: float | None, reason: str) -> None:
        event = "signal_buy" if direction.upper() == "BUY" else "signal_sell"
        self.send(event, f"{direction} signal on {symbol}", price=price, reason=reason)

    def trade_opened(self, symbol: str, direction: str, volume: float, price: float,
                     sl: float | None, tp: float | None, ticket: int) -> None:
        self.send("trade_opened", f"Opened {direction} {symbol}", ticket=ticket,
                  volume=volume, price=price, sl=sl, tp=tp)

    def trade_closed(self, symbol: str, direction: str, volume: float, profit: float,
                     reason: str, ticket: int) -> None:
        event = {"TP": "tp_hit", "SL": "sl_hit"}.get(reason.upper(), "trade_closed")
        self.send(event, f"Closed {direction} {symbol}", ticket=ticket, volume=volume,
                  profit=round(profit, 2), reason=reason)

    def daily_loss_limit(self, loss_percent: float, limit_percent: float) -> None:
        self.send("daily_loss_limit", "DAILY LOSS LIMIT REACHED",
                  loss=f"{loss_percent:.2f}%", limit=f"{limit_percent:.2f}%",
                  action="new trades blocked for today")

    def disconnected(self, reason: str) -> None:
        self.send("disconnected", "MT5 disconnected", reason=reason)

    def reconnected(self, account: int | None) -> None:
        self.send("reconnected", "MT5 reconnected", account=account)

    def critical_error(self, message: str) -> None:
        self.send("critical_error", "Critical error", detail=message[:300])


_notifier: Notifier | None = None
_notifier_lock = threading.Lock()


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        with _notifier_lock:
            if _notifier is None:
                _notifier = Notifier()
    return _notifier
