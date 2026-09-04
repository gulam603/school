"""Structured logging for the bot.

Every log record carries a *category* (INFO / SIGNAL / TRADE / WARNING / ERROR /
RISK) so the dashboard can colour and filter the log stream.  Records go to
three places at once:

* the console (human readable, colour free so it works in cmd.exe),
* a rotating file in ``logs/``,
* an in-memory ring buffer + SQLite, which is what the dashboard reads.

Example console output::

    [14:32:10] SIGNAL  EURUSD BUY - EMA20 crossed above EMA50 (RSI 61.2)
    [14:32:11] RISK    Risk calculated: 1.00% = 100.00 USD
    [14:32:11] RISK    Lot size: 0.10
    [14:32:12] TRADE   DEMO ORDER EXECUTED #123456 EURUSD BUY 0.10 @ 1.08400
    [14:32:12] TRADE   SL: 1.08200
    [14:32:12] TRADE   TP: 1.08600
"""

from __future__ import annotations

import logging
import logging.handlers
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Iterable

from backend.config import LOGS_DIR, settings

CATEGORIES = ("INFO", "SIGNAL", "TRADE", "WARNING", "ERROR", "RISK", "DEBUG")

_LOG_FILE = LOGS_DIR / "mt5_bot.log"

#: Ring buffer with the most recent records, served to the dashboard instantly.
_RING: Deque[dict[str, Any]] = deque(maxlen=2000)
_RING_LOCK = threading.Lock()

#: Callbacks invoked for every new record (WebSocket broadcast, DB persistence).
_SINKS: list[Callable[[dict[str, Any]], None]] = []
_SINKS_LOCK = threading.Lock()

_CONFIGURED = False


class _CategoryFormatter(logging.Formatter):
    """``[HH:MM:SS] CATEGORY  message`` - the format used in the terminal."""

    def format(self, record: logging.LogRecord) -> str:
        category = getattr(record, "category", record.levelname)
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        message = record.getMessage()
        symbol = getattr(record, "symbol", "") or ""
        prefix = f"{symbol} " if symbol else ""
        text = f"[{stamp}] {category:<7} {prefix}{message}"
        if record.exc_info:
            text = f"{text}\n{self.formatException(record.exc_info)}"
        return text


def configure_logging(level: str | None = None) -> None:
    """Install console + rotating file handlers exactly once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger("mt5bot")
    root.setLevel(getattr(logging, (level or settings.log_level or "INFO").upper(), logging.INFO))
    root.propagate = False
    formatter = _CategoryFormatter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    _CONFIGURED = True


def register_sink(sink: Callable[[dict[str, Any]], None]) -> None:
    """Register a callback that receives every emitted record as a dict."""
    with _SINKS_LOCK:
        if sink not in _SINKS:
            _SINKS.append(sink)


def unregister_sink(sink: Callable[[dict[str, Any]], None]) -> None:
    with _SINKS_LOCK:
        if sink in _SINKS:
            _SINKS.remove(sink)


def recent(limit: int = 200, categories: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Most recent log records, newest last."""
    wanted = {c.upper() for c in categories} if categories else None
    with _RING_LOCK:
        records = list(_RING)
    if wanted:
        records = [r for r in records if r["category"] in wanted]
    return records[-limit:]


class BotLogger:
    """Thin wrapper around ``logging`` that adds the trading categories."""

    def __init__(self, name: str = "mt5bot") -> None:
        configure_logging()
        self._log = logging.getLogger(name if name.startswith("mt5bot") else f"mt5bot.{name}")
        self._name = name

    # -- internal ------------------------------------------------------
    def _emit(
        self,
        level: int,
        category: str,
        message: str,
        symbol: str | None = None,
        details: dict[str, Any] | None = None,
        exc_info: bool = False,
    ) -> None:
        category = category.upper()
        self._log.log(
            level,
            message,
            extra={"category": category, "symbol": symbol or ""},
            exc_info=exc_info,
        )
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "message": message,
            "symbol": symbol,
            "source": self._name,
            "details": details or {},
        }
        with _RING_LOCK:
            _RING.append(record)
        with _SINKS_LOCK:
            sinks = list(_SINKS)
        for sink in sinks:
            try:
                sink(record)
            except Exception:  # a broken sink must never break trading
                self._log.debug("log sink failed", exc_info=True)

    # -- public API ----------------------------------------------------
    def debug(self, message: str, **kw: Any) -> None:
        self._emit(logging.DEBUG, "DEBUG", message, **kw)

    def info(self, message: str, **kw: Any) -> None:
        self._emit(logging.INFO, "INFO", message, **kw)

    def signal(self, message: str, **kw: Any) -> None:
        self._emit(logging.INFO, "SIGNAL", message, **kw)

    def trade(self, message: str, **kw: Any) -> None:
        self._emit(logging.INFO, "TRADE", message, **kw)

    def risk(self, message: str, **kw: Any) -> None:
        self._emit(logging.INFO, "RISK", message, **kw)

    def warning(self, message: str, **kw: Any) -> None:
        self._emit(logging.WARNING, "WARNING", message, **kw)

    def error(self, message: str, exc_info: bool = False, **kw: Any) -> None:
        self._emit(logging.ERROR, "ERROR", message, exc_info=exc_info, **kw)


def get_logger(name: str = "mt5bot") -> BotLogger:
    return BotLogger(name)
