"""System routes: health, MT5 connection, account, settings, mode."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app_state import get_state
from backend.config import LIVE_CONFIRMATION_PHRASE, TIMEFRAMES, settings
from backend.mt5_connection import discover_terminal_paths, mt5_available
from backend.mt5_errors import MT5Error
from backend.notifications import get_notifier
from backend.schemas import ConfigUpdate, ConnectRequest, SymbolOverride

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    state = get_state()
    return {
        "status": "ok",
        "demo_mode": settings.is_demo_mode,
        "mt5_package_available": mt5_available(),
        "connected": state.conn.status()["connected"],
        "bot_running": state.engine.running,
    }


@router.get("/mode")
def mode() -> dict[str, Any]:
    """Demo/live banner data - the frontend never decides this itself."""
    state = get_state()
    connection = state.conn.status()
    return {
        "demo": settings.is_demo_mode,
        "label": "DEMO" if settings.is_demo_mode else "LIVE",
        "live_unlocked": settings.live_trading_unlocked,
        "account_is_demo": connection.get("is_demo_account"),
        "account_type": connection.get("account_type"),
        "confirmation_phrase_required": LIVE_CONFIRMATION_PHRASE,
        "warning": (
            "DEMO MODE - orders are sent to the demo account configured in MetaTrader 5."
            if settings.is_demo_mode
            else "LIVE TRADING IS ACTIVE - orders use real money."
        ),
    }


@router.get("/status")
def status() -> dict[str, Any]:
    """Full dashboard snapshot."""
    return get_state().snapshot()


# ----------------------------------------------------------------------
# connection
# ----------------------------------------------------------------------
@router.get("/connection")
def connection_status() -> dict[str, Any]:
    return get_state().conn.status()


@router.get("/connection/terminals")
def terminals() -> dict[str, Any]:
    return {
        "mt5_package_available": mt5_available(),
        "configured_path": settings.mt5_terminal_path or None,
        "detected": discover_terminal_paths(),
    }


@router.post("/connection/connect")
def connect(request: ConnectRequest | None = None) -> dict[str, Any]:
    state = get_state()
    try:
        account = state.conn.connect(request.terminal_path if request else None)
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc
    try:
        state.resolver.refresh()
    except MT5Error:
        pass
    state.publish("connection", state.conn.status())
    return {"connection": state.conn.status(), "account": account}


@router.post("/connection/disconnect")
def disconnect() -> dict[str, Any]:
    state = get_state()
    if state.engine.running:
        state.engine.stop("disconnect_requested")
    state.conn.disconnect("manual")
    return state.conn.status()


@router.get("/account")
def account() -> dict[str, Any]:
    state = get_state()
    try:
        return state.conn.account_info()
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc


@router.get("/terminal")
def terminal() -> dict[str, Any]:
    try:
        return get_state().conn.terminal_info()
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc


# ----------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------
@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """Runtime config + non-secret environment info."""
    state = get_state()
    return {
        "config": state.config.to_dict(),
        "environment": settings.public_dict(),
        "timeframes": list(TIMEFRAMES),
        "notifications": get_notifier().status(),
        "terminal_paths": discover_terminal_paths(),
    }


@router.put("/settings")
def update_settings(update: ConfigUpdate) -> dict[str, Any]:
    state = get_state()
    changes = update.model_dump(exclude_none=True)
    try:
        applied = state.update_config(changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"applied": applied, "config": state.config.to_dict()}


@router.get("/settings/symbols")
def symbol_mapping() -> dict[str, Any]:
    """How each canonical symbol maps onto this broker's instrument names."""
    state = get_state()
    try:
        report = state.resolver.mapping_report(
            state.config.traded_symbols, state.config.symbol_prefix, state.config.symbol_suffix
        )
        broker_symbols = state.resolver.broker_symbols()
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc
    return {
        "mapping": report,
        "overrides": state.resolver.overrides,
        "broker_symbol_count": len(broker_symbols),
        "broker_symbols": broker_symbols[:600],
    }


@router.post("/settings/symbols/override")
def set_symbol_override(override: SymbolOverride) -> dict[str, Any]:
    state = get_state()
    state.resolver.set_override(override.symbol, override.broker_symbol)
    state.market.invalidate()
    return {"overrides": state.resolver.overrides}


@router.delete("/settings/symbols/override/{symbol}")
def clear_symbol_override(symbol: str) -> dict[str, Any]:
    state = get_state()
    state.resolver.clear_override(symbol)
    state.market.invalidate()
    return {"overrides": state.resolver.overrides}
