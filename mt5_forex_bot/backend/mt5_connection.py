"""Connection to the MetaTrader 5 terminal.

Responsibilities
----------------
* find the installed terminal (explicit path -> Windows registry -> the usual
  install locations),
* initialise / log in, using credentials that come from the environment only,
* keep the connection alive and reconnect automatically with backoff,
* expose everything the rest of the bot needs (account, symbols, quotes,
  candles, positions, orders, history, order_send) behind one lock, because the
  MetaTrader5 python API is not thread safe,
* refuse to trade on a real-money account unless the operator has explicitly
  unlocked live trading in the environment.

The MetaTrader5 package only exists on Windows.  It is therefore imported
lazily: the module can be imported anywhere (tests, Linux CI) and only the
calls that genuinely need the terminal raise ``MT5NotAvailableError``.
"""

from __future__ import annotations

import os
import platform
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from backend.config import settings
from backend.logger import get_logger
from backend.mt5_errors import (
    MT5ConnectionError,
    MT5Error,
    MT5NotAvailableError,
    MT5NotConnectedError,
    MT5TerminalNotFoundError,
)

log = get_logger("mt5")

T = TypeVar("T")

_mt5: Any = None
_import_error: str = ""

#: Where MetaTrader 5 usually lives on a Windows box.
_COMMON_TERMINAL_PATHS = (
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    r"C:\Program Files\Exness MetaTrader 5\terminal64.exe",
    r"C:\Program Files\XM Global MT5\terminal64.exe",
    r"C:\Program Files\IC Markets Global MT5\terminal64.exe",
    r"C:\Program Files\FTMO MetaTrader 5\terminal64.exe",
    r"C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe",
    r"C:\Program Files\RoboForex MT5 Terminal\terminal64.exe",
)

ACCOUNT_TRADE_MODE_NAMES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}


def load_mt5() -> Any:
    """Import the MetaTrader5 package, raising a friendly error if impossible."""
    global _mt5, _import_error
    if _mt5 is not None:
        return _mt5
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on the host OS
        _import_error = str(exc)
        raise MT5NotAvailableError(
            "The MetaTrader5 python package is not available on this machine "
            f"({exc}). It requires Windows and `pip install MetaTrader5`. "
            "The trading engine cannot run without a real MT5 terminal."
        ) from exc
    _mt5 = mt5
    return _mt5


def mt5_available() -> bool:
    try:
        load_mt5()
        return True
    except MT5NotAvailableError:
        return False


def discover_terminal_paths() -> list[str]:
    """Return every MetaTrader 5 terminal executable we can find."""
    found: list[str] = []

    configured = settings.mt5_terminal_path
    if configured and Path(configured).exists():
        found.append(str(Path(configured)))

    if platform.system() == "Windows":  # pragma: no cover - Windows only
        found.extend(_registry_terminal_paths())
        for candidate in _COMMON_TERMINAL_PATHS:
            if Path(candidate).exists():
                found.append(candidate)
        # Broker specific installs live under %APPDATA%\MetaQuotes\Terminal\<hash>
        appdata = os.getenv("APPDATA")
        if appdata:
            base = Path(appdata) / "MetaQuotes" / "Terminal"
            if base.is_dir():
                for entry in base.iterdir():
                    exe = entry / "terminal64.exe"
                    if exe.exists():
                        found.append(str(exe))
        for root in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
            if not root.is_dir():
                continue
            try:
                for entry in root.iterdir():
                    if "metatrader 5" in entry.name.lower() or "mt5" in entry.name.lower():
                        exe = entry / "terminal64.exe"
                        if exe.exists():
                            found.append(str(exe))
            except OSError:
                continue

    unique: list[str] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def _registry_terminal_paths() -> list[str]:  # pragma: no cover - Windows only
    """Read the terminal locations MetaQuotes records in the registry."""
    paths: list[str] = []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return paths

    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\MetaQuotes\Terminal"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\MetaQuotes\Terminal"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\MetaQuotes\Terminal"),
    )
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                index = 0
                while True:
                    try:
                        child = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(key, child) as child_key:
                            value, _ = winreg.QueryValueEx(child_key, "InstallLocation")
                            exe = Path(value) / "terminal64.exe"
                            if exe.exists():
                                paths.append(str(exe))
                    except OSError:
                        continue
        except OSError:
            continue
    return paths


class MT5Connection:
    """Serialised access to one MetaTrader 5 terminal."""

    #: Reconnection backoff in seconds; the last value repeats.
    BACKOFF = (2, 5, 10, 20, 30, 60)

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._connected = False
        self._terminal_path: str | None = None
        self._account_number: int | None = None
        self._server: str = ""
        self._broker: str = ""
        self._trade_mode: int | None = None
        self._connected_at: datetime | None = None
        self._last_error: str = ""
        self._reconnect_attempts = 0
        self._on_state_change: list[Callable[[bool, str], None]] = []

    # ------------------------------------------------------------------
    # observers
    # ------------------------------------------------------------------
    def on_state_change(self, callback: Callable[[bool, str], None]) -> None:
        """Register a callback fired on connect/disconnect (connected, reason)."""
        self._on_state_change.append(callback)

    def _notify(self, connected: bool, reason: str) -> None:
        for callback in list(self._on_state_change):
            try:
                callback(connected, reason)
            except Exception:
                log.error("Connection state callback failed", exc_info=True)

    # ------------------------------------------------------------------
    # connection lifecycle
    # ------------------------------------------------------------------
    def connect(self, path: str | None = None) -> dict[str, Any]:
        """Initialise the terminal and log in. Returns the account snapshot."""
        with self._lock:
            mt5 = load_mt5()

            candidates = [path] if path else []
            candidates.extend(discover_terminal_paths())
            candidates = [c for c in candidates if c]

            kwargs: dict[str, Any] = {"timeout": settings.mt5_timeout_ms}
            if settings.mt5_login:
                kwargs["login"] = int(settings.mt5_login)
                kwargs["password"] = settings.mt5_password
                kwargs["server"] = settings.mt5_server

            initialised = False
            tried: list[str] = []
            for candidate in candidates or [None]:  # type: ignore[list-item]
                if candidate:
                    tried.append(candidate)
                    initialised = mt5.initialize(candidate, **kwargs)
                else:
                    # No path known: MT5 attaches to an already running terminal.
                    initialised = mt5.initialize(**kwargs)
                if initialised:
                    self._terminal_path = candidate
                    break
                code, description = mt5.last_error()
                self._last_error = f"{description} (code {code})"
                log.debug(f"initialize() failed for {candidate or 'auto'}: {self._last_error}")

            if not initialised:
                self._connected = False
                if not candidates and platform.system() == "Windows":
                    raise MT5TerminalNotFoundError(
                        "No MetaTrader 5 terminal was found. Install MT5 or set "
                        "MT5_TERMINAL_PATH in your .env file."
                    )
                raise MT5ConnectionError(
                    f"Could not connect to MetaTrader 5: {self._last_error or 'unknown error'}. "
                    "Make sure the terminal is running and logged in"
                    + (f" (tried: {', '.join(tried)})" if tried else "")
                )

            account = mt5.account_info()
            if account is None:
                code, description = mt5.last_error()
                mt5.shutdown()
                self._connected = False
                raise MT5ConnectionError(
                    f"Terminal started but no account is logged in: {description} (code {code})"
                )

            info = account._asdict()
            self._connected = True
            self._connected_at = datetime.now(timezone.utc)
            self._account_number = int(info.get("login", 0))
            self._server = str(info.get("server", ""))
            self._broker = str(info.get("company", ""))
            self._trade_mode = int(info.get("trade_mode", 0))
            self._reconnect_attempts = 0
            self._last_error = ""

            mode = ACCOUNT_TRADE_MODE_NAMES.get(self._trade_mode, "UNKNOWN")
            log.info(
                f"Connected to MT5 - account {self._account_number} ({mode}) "
                f"on {self._server} / {self._broker}"
            )
            if mode == "REAL":
                log.warning(
                    "The logged in MT5 account is a REAL MONEY account. "
                    "Orders stay blocked unless live trading is explicitly unlocked in .env"
                )
            self._notify(True, "connected")
            return self.account_info()

    def disconnect(self, reason: str = "manual") -> None:
        with self._lock:
            if _mt5 is not None and self._connected:
                try:
                    _mt5.shutdown()
                except Exception:
                    log.debug("shutdown() raised", exc_info=True)
            was_connected = self._connected
            self._connected = False
            self._connected_at = None
            if was_connected:
                log.info(f"Disconnected from MT5 ({reason})")
                self._notify(False, reason)

    def is_connected(self) -> bool:
        """True when the terminal still answers. Cheap enough to poll."""
        with self._lock:
            if not self._connected or _mt5 is None:
                return False
            try:
                terminal = _mt5.terminal_info()
            except Exception:
                terminal = None
            if terminal is None:
                self._connected = False
                self._last_error = "terminal stopped responding"
                log.warning("MT5 terminal stopped responding")
                self._notify(False, "terminal_lost")
                return False
            return bool(getattr(terminal, "connected", True))

    def ensure_connected(self) -> bool:
        """Reconnect if needed. Returns True when usable, never raises."""
        if self.is_connected():
            return True
        delay = self.BACKOFF[min(self._reconnect_attempts, len(self.BACKOFF) - 1)]
        self._reconnect_attempts += 1
        log.warning(
            f"MT5 not connected - reconnect attempt {self._reconnect_attempts} "
            f"(next retry in {delay}s if this fails)"
        )
        try:
            self.connect(self._terminal_path)
            return True
        except MT5Error as exc:
            self._last_error = exc.message
            log.error(f"Reconnect failed: {exc.message}")
            return False

    def reconnect_delay(self) -> int:
        return self.BACKOFF[min(self._reconnect_attempts, len(self.BACKOFF) - 1)]

    # ------------------------------------------------------------------
    # guarded call helper
    # ------------------------------------------------------------------
    def call(self, func_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call an MT5 function under the lock, checking the connection first."""
        with self._lock:
            if not self._connected:
                raise MT5NotConnectedError("MetaTrader 5 is not connected")
            mt5 = load_mt5()
            func = getattr(mt5, func_name, None)
            if func is None:
                raise MT5Error(f"MetaTrader5 has no function '{func_name}'")
            return func(*args, **kwargs)

    def last_error(self) -> tuple[int, str]:
        try:
            mt5 = load_mt5()
            code, description = mt5.last_error()
            return int(code), str(description)
        except MT5Error:
            return 0, self._last_error

    # ------------------------------------------------------------------
    # account / terminal information
    # ------------------------------------------------------------------
    def account_info(self) -> dict[str, Any]:
        """Balance, equity, margin and the demo/live flag."""
        with self._lock:
            if not self._connected:
                raise MT5NotConnectedError("MetaTrader 5 is not connected")
            info = load_mt5().account_info()
            if info is None:
                self._connected = False
                raise MT5NotConnectedError("account_info() returned nothing - connection lost")
            data = info._asdict()
            trade_mode = int(data.get("trade_mode", 0))
            return {
                "login": int(data.get("login", 0)),
                "name": data.get("name", ""),
                "server": data.get("server", ""),
                "broker": data.get("company", ""),
                "currency": data.get("currency", "USD"),
                "leverage": int(data.get("leverage", 0)),
                "balance": float(data.get("balance", 0.0)),
                "equity": float(data.get("equity", 0.0)),
                "margin": float(data.get("margin", 0.0)),
                "free_margin": float(data.get("margin_free", 0.0)),
                "margin_level": float(data.get("margin_level", 0.0)),
                "profit": float(data.get("profit", 0.0)),
                "trade_mode": trade_mode,
                "account_type": ACCOUNT_TRADE_MODE_NAMES.get(trade_mode, "UNKNOWN"),
                "is_demo": trade_mode != 2,
                "trade_allowed": bool(data.get("trade_allowed", False)),
                "trade_expert": bool(data.get("trade_expert", False)),
            }

    def terminal_info(self) -> dict[str, Any]:
        with self._lock:
            if not self._connected:
                raise MT5NotConnectedError("MetaTrader 5 is not connected")
            info = load_mt5().terminal_info()
            if info is None:
                self._connected = False
                raise MT5NotConnectedError("terminal_info() returned nothing - connection lost")
            data = info._asdict()
            return {
                "name": data.get("name", ""),
                "company": data.get("company", ""),
                "path": data.get("path", ""),
                "data_path": data.get("data_path", ""),
                "build": int(data.get("build", 0)),
                "connected": bool(data.get("connected", False)),
                "trade_allowed": bool(data.get("trade_allowed", False)),
                "dlls_allowed": bool(data.get("dlls_allowed", False)),
                "ping_last_ms": round(float(data.get("ping_last", 0)) / 1000.0, 2),
            }

    def status(self) -> dict[str, Any]:
        """Connection snapshot for the dashboard top bar (no secrets)."""
        connected = self._connected
        uptime = (
            int((datetime.now(timezone.utc) - self._connected_at).total_seconds())
            if self._connected_at and connected
            else 0
        )
        payload: dict[str, Any] = {
            "connected": connected,
            "mt5_package_available": mt5_available(),
            "terminal_path": self._terminal_path,
            "account_number": self._account_number,
            "server": self._server,
            "broker": self._broker,
            "account_type": ACCOUNT_TRADE_MODE_NAMES.get(self._trade_mode or 0, "UNKNOWN")
            if self._trade_mode is not None
            else None,
            "is_demo_account": (self._trade_mode != 2) if self._trade_mode is not None else None,
            "demo_mode": settings.is_demo_mode,
            "live_trading_unlocked": settings.live_trading_unlocked,
            "connection_uptime_seconds": uptime,
            "connected_since": self._connected_at.isoformat() if self._connected_at else None,
            "last_error": self._last_error,
            "reconnect_attempts": self._reconnect_attempts,
            "server_time": datetime.now().isoformat(timespec="seconds"),
        }
        if connected:
            try:
                terminal = self.terminal_info()
                payload["terminal_build"] = terminal["build"]
                payload["terminal_trade_allowed"] = terminal["trade_allowed"]
                payload["ping_ms"] = terminal["ping_last_ms"]
            except MT5Error:
                pass
        return payload

    # ------------------------------------------------------------------
    # trading permission
    # ------------------------------------------------------------------
    @property
    def is_demo_account(self) -> bool | None:
        if self._trade_mode is None:
            return None
        return self._trade_mode != 2

    def trading_mode(self) -> str:
        return "live" if settings.live_trading_unlocked else "demo"

    # ------------------------------------------------------------------
    # convenience wrappers used by market_data / order_manager
    # ------------------------------------------------------------------
    def symbols_get(self) -> tuple[Any, ...]:
        return self.call("symbols_get") or ()

    def symbol_info(self, symbol: str) -> Any:
        return self.call("symbol_info", symbol)

    def symbol_info_tick(self, symbol: str) -> Any:
        return self.call("symbol_info_tick", symbol)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return bool(self.call("symbol_select", symbol, enable))

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any:
        return self.call("copy_rates_from_pos", symbol, timeframe, start, count)

    def copy_rates_range(self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime) -> Any:
        return self.call("copy_rates_range", symbol, timeframe, date_from, date_to)

    def positions_get(self, **kwargs: Any) -> tuple[Any, ...]:
        return self.call("positions_get", **kwargs) or ()

    def orders_get(self, **kwargs: Any) -> tuple[Any, ...]:
        return self.call("orders_get", **kwargs) or ()

    def history_deals_get(self, date_from: datetime, date_to: datetime, **kwargs: Any) -> tuple[Any, ...]:
        return self.call("history_deals_get", date_from, date_to, **kwargs) or ()

    def history_orders_get(self, date_from: datetime, date_to: datetime, **kwargs: Any) -> tuple[Any, ...]:
        return self.call("history_orders_get", date_from, date_to, **kwargs) or ()

    def order_send(self, request: dict[str, Any]) -> Any:
        return self.call("order_send", request)

    def order_check(self, request: dict[str, Any]) -> Any:
        return self.call("order_check", request)

    def order_calc_margin(self, action: int, symbol: str, volume: float, price: float) -> float | None:
        value = self.call("order_calc_margin", action, symbol, volume, price)
        return float(value) if value is not None else None

    def order_calc_profit(
        self, action: int, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        value = self.call("order_calc_profit", action, symbol, volume, price_open, price_close)
        return float(value) if value is not None else None


_connection: MT5Connection | None = None
_connection_lock = threading.Lock()


def get_connection() -> MT5Connection:
    """Process-wide MT5 connection singleton."""
    global _connection
    if _connection is None:
        with _connection_lock:
            if _connection is None:
                _connection = MT5Connection()
    return _connection


# ----------------------------------------------------------------------
# timeframe helpers
# ----------------------------------------------------------------------
#: Minutes per supported timeframe - usable without MT5 installed.
TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


def timeframe_to_mt5(timeframe: str) -> int:
    """Map 'H1' to ``mt5.TIMEFRAME_H1``."""
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe '{timeframe}'")
    mt5 = load_mt5()
    return int(getattr(mt5, f"TIMEFRAME_{timeframe}"))


def timeframe_seconds(timeframe: str) -> int:
    return TIMEFRAME_MINUTES[timeframe.upper()] * 60


def bars_needed(timeframe: str, days: int) -> int:
    """Rough number of bars covering ``days`` calendar days (forex ~5/7 days)."""
    per_day = (24 * 60) / TIMEFRAME_MINUTES[timeframe.upper()]
    return int(per_day * days * 5 / 7) + 10


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def broker_now(offset_hours: float = 0.0) -> datetime:
    return utc_now() + timedelta(hours=offset_hours)


def wait_backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> None:
    """Sleep with exponential backoff - used by retrying callers."""
    time.sleep(min(cap, base * (2 ** max(0, attempt - 1))))
