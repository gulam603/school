"""Request/response models for the local API.

Note what is *not* here: there is no model that accepts or returns an MT5
password, a Telegram token or any other secret.  Credentials live in ``.env``
and never cross the HTTP boundary.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    terminal_path: str | None = Field(
        default=None, description="Optional override for the terminal64.exe path"
    )


class ConfigUpdate(BaseModel):
    """Partial update of the runtime configuration (Settings page)."""

    symbol: str | None = None
    timeframe: str | None = None
    strategy: str | None = None
    traded_symbols: list[str] | None = None
    symbol_prefix: str | None = None
    symbol_suffix: str | None = None
    magic_number: int | None = None
    deviation_points: int | None = Field(default=None, ge=0, le=1000)
    poll_interval_seconds: int | None = Field(default=None, ge=1, le=3600)

    risk_per_trade_percent: float | None = Field(default=None, gt=0, le=10)
    max_daily_loss_percent: float | None = Field(default=None, gt=0, le=50)
    max_open_trades: int | None = Field(default=None, ge=1, le=50)
    min_risk_reward: float | None = Field(default=None, gt=0, le=20)
    atr_period: int | None = Field(default=None, ge=2, le=200)
    atr_sl_multiplier: float | None = Field(default=None, gt=0, le=20)
    max_lot_size: float | None = Field(default=None, gt=0, le=500)
    min_lot_size: float | None = Field(default=None, ge=0, le=500)
    max_spread_points: int | None = Field(default=None, ge=0, le=10000)

    break_even_enabled: bool | None = None
    break_even_trigger_r: float | None = Field(default=None, gt=0, le=20)
    break_even_offset_points: int | None = Field(default=None, ge=0, le=10000)
    trailing_stop_enabled: bool | None = None
    trailing_start_r: float | None = Field(default=None, gt=0, le=20)
    trailing_distance_atr: float | None = Field(default=None, gt=0, le=20)
    partial_close_enabled: bool | None = None
    partial_close_at_r: float | None = Field(default=None, gt=0, le=20)
    partial_close_percent: float | None = Field(default=None, gt=0, lt=100)
    max_holding_hours: int | None = Field(default=None, ge=0, le=8760)

    ema_fast: int | None = Field(default=None, ge=2, le=1000)
    ema_slow: int | None = Field(default=None, ge=3, le=5000)
    use_rsi_filter: bool | None = None
    rsi_period: int | None = Field(default=None, ge=2, le=200)
    rsi_buy_max: float | None = Field(default=None, gt=0, le=100)
    rsi_sell_min: float | None = Field(default=None, ge=0, lt=100)

    telegram_notifications: bool | None = None


class SymbolOverride(BaseModel):
    symbol: str
    broker_symbol: str


class ModifyPositionRequest(BaseModel):
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)


class ClosePositionRequest(BaseModel):
    volume: float | None = Field(default=None, gt=0, description="Partial close volume")
    reason: str = "manual"


class CloseAllRequest(BaseModel):
    only_bot_positions: bool = False
    reason: str = "manual_close_all"


class BacktestRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "H1"
    start_date: date
    end_date: date
    initial_balance: float = Field(default=10_000.0, gt=0)
    risk_percent: float = Field(default=1.0, gt=0, le=10)
    strategy: str | None = None
    params: dict[str, Any] | None = None


class BotStartRequest(BaseModel):
    confirm: bool = True
