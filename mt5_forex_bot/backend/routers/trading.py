"""Trading routes: bot control, positions, orders, manual close."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app_state import get_state
from backend.mt5_errors import LiveTradingBlockedError, MT5Error
from backend.schemas import (
    BotStartRequest,
    CloseAllRequest,
    ClosePositionRequest,
    ModifyPositionRequest,
)

router = APIRouter(tags=["trading"])


def _error(exc: MT5Error) -> HTTPException:
    status = 403 if isinstance(exc, LiveTradingBlockedError) else 503
    return HTTPException(status_code=status, detail=exc.as_dict())


# ----------------------------------------------------------------------
# bot control
# ----------------------------------------------------------------------
@router.get("/bot/status")
def bot_status() -> dict[str, Any]:
    return get_state().engine.status()


@router.post("/bot/start")
def start_bot(request: BotStartRequest | None = None) -> dict[str, Any]:
    state = get_state()
    try:
        return state.engine.start()
    except MT5Error as exc:
        raise _error(exc) from exc
    except ValueError as exc:  # unknown strategy
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/bot/stop")
def stop_bot() -> dict[str, Any]:
    return get_state().engine.stop("manual")


@router.post("/bot/emergency-stop")
def emergency_stop() -> dict[str, Any]:
    """Stop the bot AND close every open position."""
    return get_state().engine.emergency_stop()


# ----------------------------------------------------------------------
# positions
# ----------------------------------------------------------------------
@router.get("/positions")
def positions(only_bot: bool = False) -> dict[str, Any]:
    state = get_state()
    try:
        rows = state.engine.positions.list_positions(only_bot=only_bot)
    except MT5Error as exc:
        raise _error(exc) from exc
    return {
        "positions": rows,
        "count": len(rows),
        "total_profit": round(sum(r["profit"] + r["swap"] for r in rows), 2),
    }


@router.put("/positions/{ticket}")
def modify_position(ticket: int, request: ModifyPositionRequest) -> dict[str, Any]:
    state = get_state()
    try:
        result = state.engine.orders.modify_position(
            ticket, stop_loss=request.stop_loss, take_profit=request.take_profit
        )
    except MT5Error as exc:
        raise _error(exc) from exc
    state.publish("position_modified", result.to_dict())
    return result.to_dict()


@router.post("/positions/{ticket}/close")
def close_position(ticket: int, request: ClosePositionRequest | None = None) -> dict[str, Any]:
    state = get_state()
    request = request or ClosePositionRequest()
    try:
        result = state.engine.orders.close_position(
            ticket, volume=request.volume, reason=request.reason
        )
    except MT5Error as exc:
        raise _error(exc) from exc
    state.publish("position_closed", result.to_dict())
    return result.to_dict()


@router.post("/positions/close-all")
def close_all(request: CloseAllRequest | None = None) -> dict[str, Any]:
    state = get_state()
    request = request or CloseAllRequest()
    try:
        result = state.engine.orders.close_all(
            only_bot_positions=request.only_bot_positions, reason=request.reason
        )
    except MT5Error as exc:
        raise _error(exc) from exc
    state.publish("positions_closed", result)
    return result


# ----------------------------------------------------------------------
# orders
# ----------------------------------------------------------------------
@router.get("/orders/pending")
def pending_orders() -> dict[str, Any]:
    """Pending (not yet filled) orders sitting in the terminal."""
    state = get_state()
    try:
        orders = state.conn.orders_get()
    except MT5Error as exc:
        raise _error(exc) from exc

    rows = []
    for order in orders:
        rows.append(
            {
                "ticket": int(order.ticket),
                "symbol": state.market.to_canonical(str(order.symbol)),
                "broker_symbol": str(order.symbol),
                "type": int(order.type),
                "volume": float(getattr(order, "volume_current", 0.0)),
                "price_open": float(getattr(order, "price_open", 0.0)),
                "stop_loss": float(getattr(order, "sl", 0.0)) or None,
                "take_profit": float(getattr(order, "tp", 0.0)) or None,
                "magic": int(getattr(order, "magic", 0)),
                "comment": str(getattr(order, "comment", "")),
            }
        )
    return {"orders": rows, "count": len(rows)}


@router.get("/orders/log")
def order_log(limit: int = 100) -> dict[str, Any]:
    """Every order request the bot sent and what the server answered."""
    return {"orders": get_state().db.recent_orders(limit=min(limit, 500))}
