"""Configuration validation and the demo/live safety gate."""

from __future__ import annotations

import pytest

from backend.config import LIVE_CONFIRMATION_PHRASE, BotConfig, Settings


# ----------------------------------------------------------------------
# demo / live gate
# ----------------------------------------------------------------------
def test_the_default_installation_is_demo_only():
    settings = Settings()
    assert settings.is_demo_mode is True
    assert settings.live_trading_unlocked is False


@pytest.mark.parametrize(
    "mode,allow,phrase",
    [
        ("live", True, ""),                              # missing phrase
        ("live", False, LIVE_CONFIRMATION_PHRASE),       # flag not set
        ("demo", True, LIVE_CONFIRMATION_PHRASE),        # mode still demo
        ("live", True, "i understand the risk"),         # wrong case
        ("live", True, "yes"),                           # wrong phrase
    ],
)
def test_live_trading_needs_all_three_switches(mode, allow, phrase):
    settings = Settings(trading_mode=mode, allow_live_trading=allow, live_trading_confirmation=phrase)
    assert settings.live_trading_unlocked is False
    assert settings.is_demo_mode is True


def test_live_trading_unlocks_only_with_the_full_combination():
    settings = Settings(
        trading_mode="live", allow_live_trading=True,
        live_trading_confirmation=LIVE_CONFIRMATION_PHRASE,
    )
    assert settings.live_trading_unlocked is True
    assert settings.is_demo_mode is False


def test_public_settings_never_expose_secrets():
    settings = Settings(
        mt5_password="hunter2", mt5_login=12345, telegram_bot_token="secret-token",
        telegram_chat_id="42", mt5_terminal_path=r"C:\\MT5\\terminal64.exe",
    )
    payload = settings.public_dict()
    serialised = str(payload)
    assert "hunter2" not in serialised
    assert "secret-token" not in serialised
    assert "password" not in payload
    assert payload["mt5_login_configured"] is True     # only the fact, not the value
    assert payload["telegram_configured"] is True


# ----------------------------------------------------------------------
# runtime configuration
# ----------------------------------------------------------------------
def test_defaults_match_the_specification():
    config = BotConfig()
    assert config.risk_per_trade_percent == 1.0
    assert config.max_daily_loss_percent == 5.0
    assert config.max_open_trades == 5
    assert config.min_risk_reward == 2.0
    assert config.symbol == "EURUSD"
    assert config.timeframe == "H1"
    assert config.strategy == "ema_crossover"


def test_an_unsupported_timeframe_is_rejected():
    config = BotConfig(timeframe="M2")
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        config.validate()


def test_a_fast_ema_above_the_slow_one_is_rejected():
    config = BotConfig(ema_fast=50, ema_slow=20)
    with pytest.raises(ValueError, match="EMA fast period"):
        config.validate()


def test_values_are_clamped_into_a_sane_range():
    config = BotConfig(risk_per_trade_percent=500.0, max_open_trades=9999)
    config.validate()
    assert config.risk_per_trade_percent <= 10.0
    assert config.max_open_trades <= 50


def test_a_rejected_update_rolls_everything_back():
    """A refused settings change must not leave a half-applied configuration."""
    config = BotConfig()
    config.validate()
    before = config.to_dict()
    with pytest.raises(ValueError):
        config.update({"risk_per_trade_percent": 2.0, "ema_fast": 100, "ema_slow": 50})
    assert config.to_dict() == before


def test_a_valid_update_reports_the_changed_fields():
    config = BotConfig()
    config.validate()
    applied = config.update({"risk_per_trade_percent": 2.0, "max_open_trades": 3})
    assert set(applied) == {"risk_per_trade_percent", "max_open_trades"}
    assert config.risk_per_trade_percent == 2.0


def test_unknown_keys_are_ignored():
    config = BotConfig()
    config.validate()
    assert config.update({"__class__": "evil", "not_a_field": 1}) == []


def test_strategy_params_are_forwarded_to_the_strategy():
    config = BotConfig(ema_fast=8, ema_slow=21, rsi_period=7)
    config.validate()
    params = config.strategy_params()
    assert params["ema_fast"] == 8
    assert params["ema_slow"] == 21
    assert params["rsi_period"] == 7
