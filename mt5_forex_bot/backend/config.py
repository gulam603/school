"""Configuration for the MT5 Forex Bot.

Two layers of configuration exist:

``Settings``
    Environment backed, read once at process start. Holds everything that is
    either a secret (MT5 password, Telegram token) or that cannot be changed
    while the process runs (ports, database path, live-trading unlock).
    Secrets live here and are **never** serialised to the API.

``BotConfig``
    Runtime configuration edited from the dashboard Settings page and persisted
    in SQLite. Contains no secrets, so it can be sent to the frontend as-is.

The separation is deliberate: the frontend can freely read/write ``BotConfig``
while ``Settings`` (with the credentials) never leaves the backend process.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Project root = the directory that contains backend/, strategies/, data/ ...
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"

load_dotenv(ROOT_DIR / ".env")

LIVE_CONFIRMATION_PHRASE = "I UNDERSTAND THE RISK OF LIVE TRADING"

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

DEFAULT_SYMBOLS = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "XAUUSD",
)


def _env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip()


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw == "":
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_opt_float(key: str) -> float | None:
    raw = _env(key)
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_list(key: str, default: tuple[str, ...]) -> list[str]:
    raw = _env(key)
    if raw == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Process level settings sourced from the environment / .env file."""

    # --- mode -------------------------------------------------------------
    trading_mode: str = "demo"
    allow_live_trading: bool = False
    live_trading_confirmation: str = ""

    # --- MT5 terminal (secrets) ------------------------------------------
    mt5_terminal_path: str = ""
    mt5_login: int | None = None
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_timeout_ms: int = 60_000
    mt5_require_algo_trading: bool = True

    # --- server -----------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    database_path: str = "data/mt5_bot.db"

    # --- notification secrets --------------------------------------------
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            trading_mode=_env("TRADING_MODE", "demo").lower() or "demo",
            allow_live_trading=_env_bool("ALLOW_LIVE_TRADING", False),
            live_trading_confirmation=_env("LIVE_TRADING_CONFIRMATION"),
            mt5_terminal_path=_env("MT5_TERMINAL_PATH"),
            mt5_login=(_env_int("MT5_LOGIN", 0) or None),
            mt5_password=_env("MT5_PASSWORD"),
            mt5_server=_env("MT5_SERVER"),
            mt5_timeout_ms=_env_int("MT5_TIMEOUT_MS", 60_000),
            mt5_require_algo_trading=_env_bool("MT5_REQUIRE_ALGO_TRADING", True),
            api_host=_env("API_HOST", "127.0.0.1"),
            api_port=_env_int("API_PORT", 8000),
            cors_origins=_env_list(
                "CORS_ORIGINS",
                ("http://localhost:5173", "http://127.0.0.1:5173"),
            ),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
            database_path=_env("DATABASE_PATH", "data/mt5_bot.db"),
            telegram_enabled=_env_bool("TELEGRAM_ENABLED", False),
            telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        )

    # ------------------------------------------------------------------
    @property
    def db_file(self) -> Path:
        path = Path(self.database_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def live_trading_unlocked(self) -> bool:
        """Live trading needs three independent switches to all be flipped.

        A default installation can therefore never place a real money order by
        accident: the mode has to be ``live``, the escape hatch has to be armed
        and the confirmation phrase has to be typed out in full.
        """
        return (
            self.trading_mode == "live"
            and self.allow_live_trading
            and self.live_trading_confirmation.strip() == LIVE_CONFIRMATION_PHRASE
        )

    @property
    def is_demo_mode(self) -> bool:
        return not self.live_trading_unlocked

    def public_dict(self) -> dict[str, Any]:
        """Settings safe to expose over the API - no secrets in here."""
        return {
            "trading_mode": "live" if self.live_trading_unlocked else "demo",
            "demo_mode": self.is_demo_mode,
            "live_trading_unlocked": self.live_trading_unlocked,
            "allow_live_trading": self.allow_live_trading,
            "mt5_terminal_path_configured": bool(self.mt5_terminal_path),
            "mt5_login_configured": self.mt5_login is not None,
            "mt5_server": self.mt5_server,
            "telegram_configured": bool(self.telegram_bot_token and self.telegram_chat_id),
            "api_host": self.api_host,
            "api_port": self.api_port,
            "log_level": self.log_level,
        }


@dataclass
class BotConfig:
    """Runtime, user editable configuration (persisted in SQLite)."""

    # --- market -----------------------------------------------------------
    symbol: str = "EURUSD"
    timeframe: str = "H1"
    strategy: str = "ema_crossover"
    traded_symbols: list[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    symbol_prefix: str = ""
    symbol_suffix: str = ""
    magic_number: int = 778_899
    deviation_points: int = 20
    poll_interval_seconds: int = 5

    # --- risk -------------------------------------------------------------
    risk_per_trade_percent: float = 1.0
    max_daily_loss_percent: float = 5.0
    max_open_trades: int = 5
    min_risk_reward: float = 2.0
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    max_lot_size: float = 1.0
    min_lot_size: float | None = None
    max_spread_points: int = 30

    # --- position management ---------------------------------------------
    break_even_enabled: bool = True
    break_even_trigger_r: float = 1.0
    break_even_offset_points: int = 20
    trailing_stop_enabled: bool = True
    trailing_start_r: float = 1.5
    trailing_distance_atr: float = 1.0
    partial_close_enabled: bool = False
    partial_close_at_r: float = 1.0
    partial_close_percent: float = 50.0
    max_holding_hours: int = 48

    # --- strategy parameters ---------------------------------------------
    ema_fast: int = 20
    ema_slow: int = 50
    use_rsi_filter: bool = True
    rsi_period: int = 14
    rsi_buy_max: float = 70.0
    rsi_sell_min: float = 30.0

    # --- notifications (flag only, credentials stay in .env) --------------
    telegram_notifications: bool = False

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            symbol=_env("DEFAULT_SYMBOL", "EURUSD").upper(),
            timeframe=_env("DEFAULT_TIMEFRAME", "H1").upper(),
            strategy=_env("DEFAULT_STRATEGY", "ema_crossover"),
            traded_symbols=[s.upper() for s in _env_list("TRADED_SYMBOLS", DEFAULT_SYMBOLS)],
            symbol_prefix=_env("SYMBOL_PREFIX"),
            symbol_suffix=_env("SYMBOL_SUFFIX"),
            magic_number=_env_int("MAGIC_NUMBER", 778_899),
            deviation_points=_env_int("DEVIATION_POINTS", 20),
            poll_interval_seconds=max(1, _env_int("POLL_INTERVAL_SECONDS", 5)),
            risk_per_trade_percent=_env_float("RISK_PER_TRADE_PERCENT", 1.0),
            max_daily_loss_percent=_env_float("MAX_DAILY_LOSS_PERCENT", 5.0),
            max_open_trades=_env_int("MAX_OPEN_TRADES", 5),
            min_risk_reward=_env_float("MIN_RISK_REWARD", 2.0),
            atr_period=_env_int("ATR_PERIOD", 14),
            atr_sl_multiplier=_env_float("ATR_SL_MULTIPLIER", 1.5),
            max_lot_size=_env_float("MAX_LOT_SIZE", 1.0),
            min_lot_size=_env_opt_float("MIN_LOT_SIZE"),
            max_spread_points=_env_int("MAX_SPREAD_POINTS", 30),
            break_even_enabled=_env_bool("BREAK_EVEN_ENABLED", True),
            break_even_trigger_r=_env_float("BREAK_EVEN_TRIGGER_R", 1.0),
            break_even_offset_points=_env_int("BREAK_EVEN_OFFSET_POINTS", 20),
            trailing_stop_enabled=_env_bool("TRAILING_STOP_ENABLED", True),
            trailing_start_r=_env_float("TRAILING_START_R", 1.5),
            trailing_distance_atr=_env_float("TRAILING_DISTANCE_ATR", 1.0),
            partial_close_enabled=_env_bool("PARTIAL_CLOSE_ENABLED", False),
            partial_close_at_r=_env_float("PARTIAL_CLOSE_AT_R", 1.0),
            partial_close_percent=_env_float("PARTIAL_CLOSE_PERCENT", 50.0),
            max_holding_hours=_env_int("MAX_HOLDING_HOURS", 48),
            ema_fast=_env_int("EMA_FAST", 20),
            ema_slow=_env_int("EMA_SLOW", 50),
            use_rsi_filter=_env_bool("USE_RSI_FILTER", True),
            rsi_period=_env_int("RSI_PERIOD", 14),
            rsi_buy_max=_env_float("RSI_BUY_MAX", 70.0),
            rsi_sell_min=_env_float("RSI_SELL_MIN", 30.0),
            telegram_notifications=_env_bool("TELEGRAM_ENABLED", False),
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def update(self, changes: dict[str, Any]) -> list[str]:
        """Apply a partial update, returning the names of the changed fields.

        The update is transactional: unknown keys are ignored (so a newer
        frontend cannot inject arbitrary attributes) and if validation rejects
        the result, every field is rolled back to its previous value. A refused
        change must never leave the running bot half-configured.
        """
        known = {f.name for f in fields(self)}
        previous = {name: getattr(self, name) for name in known}
        applied: list[str] = []
        try:
            for key, value in changes.items():
                if key not in known or value is None:
                    continue
                if getattr(self, key) == value:
                    continue
                setattr(self, key, value)
                applied.append(key)
            self.validate()
        except Exception:
            for name, value in previous.items():
                object.__setattr__(self, name, value)
            raise
        return applied

    def validate(self) -> None:
        """Clamp every field into a sane range. Raises on impossible values."""
        self.symbol = str(self.symbol).upper().strip() or "EURUSD"
        self.timeframe = str(self.timeframe).upper().strip()
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe '{self.timeframe}'. Use one of {list(TIMEFRAMES)}")
        self.traded_symbols = [str(s).upper().strip() for s in self.traded_symbols if str(s).strip()]
        if not self.traded_symbols:
            self.traded_symbols = list(DEFAULT_SYMBOLS)

        self.risk_per_trade_percent = _clamp(float(self.risk_per_trade_percent), 0.01, 10.0)
        self.max_daily_loss_percent = _clamp(float(self.max_daily_loss_percent), 0.1, 50.0)
        self.max_open_trades = int(_clamp(int(self.max_open_trades), 1, 50))
        self.min_risk_reward = _clamp(float(self.min_risk_reward), 0.1, 20.0)
        self.atr_period = int(_clamp(int(self.atr_period), 2, 200))
        self.atr_sl_multiplier = _clamp(float(self.atr_sl_multiplier), 0.1, 20.0)
        self.max_lot_size = _clamp(float(self.max_lot_size), 0.01, 500.0)
        if self.min_lot_size is not None:
            self.min_lot_size = _clamp(float(self.min_lot_size), 0.0, self.max_lot_size)
        self.max_spread_points = int(_clamp(int(self.max_spread_points), 0, 10_000))
        self.deviation_points = int(_clamp(int(self.deviation_points), 0, 1_000))
        self.poll_interval_seconds = int(_clamp(int(self.poll_interval_seconds), 1, 3_600))
        self.magic_number = int(self.magic_number)

        self.break_even_trigger_r = _clamp(float(self.break_even_trigger_r), 0.1, 20.0)
        self.break_even_offset_points = int(_clamp(int(self.break_even_offset_points), 0, 10_000))
        self.trailing_start_r = _clamp(float(self.trailing_start_r), 0.1, 20.0)
        self.trailing_distance_atr = _clamp(float(self.trailing_distance_atr), 0.1, 20.0)
        self.partial_close_at_r = _clamp(float(self.partial_close_at_r), 0.1, 20.0)
        self.partial_close_percent = _clamp(float(self.partial_close_percent), 1.0, 95.0)
        self.max_holding_hours = int(_clamp(int(self.max_holding_hours), 0, 24 * 365))

        self.ema_fast = int(_clamp(int(self.ema_fast), 2, 1_000))
        self.ema_slow = int(_clamp(int(self.ema_slow), 3, 5_000))
        if self.ema_fast >= self.ema_slow:
            raise ValueError("EMA fast period must be smaller than the EMA slow period")
        self.rsi_period = int(_clamp(int(self.rsi_period), 2, 200))
        self.rsi_buy_max = _clamp(float(self.rsi_buy_max), 1.0, 100.0)
        self.rsi_sell_min = _clamp(float(self.rsi_sell_min), 0.0, 99.0)

        self.break_even_enabled = bool(self.break_even_enabled)
        self.trailing_stop_enabled = bool(self.trailing_stop_enabled)
        self.partial_close_enabled = bool(self.partial_close_enabled)
        self.use_rsi_filter = bool(self.use_rsi_filter)
        self.telegram_notifications = bool(self.telegram_notifications)

    def strategy_params(self) -> dict[str, Any]:
        """Parameters handed to the strategy implementation."""
        return {
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "use_rsi_filter": self.use_rsi_filter,
            "rsi_period": self.rsi_period,
            "rsi_buy_max": self.rsi_buy_max,
            "rsi_sell_min": self.rsi_sell_min,
            "atr_period": self.atr_period,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


settings = Settings.from_env()

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
