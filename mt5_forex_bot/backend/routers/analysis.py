"""History, performance, strategies, backtesting and logs."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app_state import get_state
from backend.backtester import DISCLAIMER
from backend.logger import CATEGORIES, recent as recent_logs
from backend.mt5_errors import MT5Error
from backend.schemas import BacktestRequest
from strategies import available_strategies

router = APIRouter(tags=["analysis"])


# ----------------------------------------------------------------------
# history & performance
# ----------------------------------------------------------------------
@router.get("/history/trades")
def trade_history(limit: int = Query(default=200, ge=1, le=1000), symbol: str | None = None) -> dict[str, Any]:
    state = get_state()
    return {
        "trades": state.db.recent_trades(limit=limit, symbol=symbol.upper() if symbol else None),
        "statistics": state.db.trade_statistics(),
    }


@router.get("/history/deals")
def deal_history(days: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    """Raw deal history straight from the MT5 terminal."""
    state = get_state()
    start = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        deals = state.conn.history_deals_get(start, datetime.now(timezone.utc))
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc

    rows = []
    for deal in deals:
        rows.append(
            {
                "ticket": int(deal.ticket),
                "order": int(getattr(deal, "order", 0)),
                "position": int(getattr(deal, "position_id", 0)),
                "time": datetime.fromtimestamp(int(deal.time), tz=timezone.utc).isoformat(),
                "symbol": str(deal.symbol),
                "type": int(deal.type),
                "entry": int(getattr(deal, "entry", 0)),
                "volume": float(getattr(deal, "volume", 0.0)),
                "price": float(getattr(deal, "price", 0.0)),
                "profit": float(getattr(deal, "profit", 0.0)),
                "commission": float(getattr(deal, "commission", 0.0)),
                "swap": float(getattr(deal, "swap", 0.0)),
                "comment": str(getattr(deal, "comment", "")),
                "magic": int(getattr(deal, "magic", 0)),
            }
        )
    return {"deals": rows, "count": len(rows), "days": days}


@router.get("/performance/daily")
def daily_performance(limit: int = Query(default=60, ge=1, le=365)) -> dict[str, Any]:
    state = get_state()
    return {"days": state.db.daily_history(limit=limit)}


@router.get("/signals")
def signals(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    return {"signals": get_state().db.recent_signals(limit=limit)}


@router.get("/strategy/runs")
def strategy_runs(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    return {"runs": get_state().db.recent_strategy_runs(limit=limit)}


# ----------------------------------------------------------------------
# strategies
# ----------------------------------------------------------------------
@router.get("/strategies")
def strategies() -> dict[str, Any]:
    state = get_state()
    return {
        "strategies": available_strategies(),
        "active": state.config.strategy,
        "params": state.config.strategy_params(),
        "current_signals": state.engine.status().get("current_signals", {}),
    }


# ----------------------------------------------------------------------
# backtesting
# ----------------------------------------------------------------------
@router.post("/backtest/run")
def run_backtest(request: BacktestRequest) -> dict[str, Any]:
    state = get_state()
    if request.end_date <= request.start_date:
        raise HTTPException(status_code=422, detail="End date must be after the start date")

    start = datetime.combine(request.start_date, time.min).replace(tzinfo=timezone.utc)
    end = datetime.combine(request.end_date, time.max).replace(tzinfo=timezone.utc)
    try:
        return state.backtester.run(
            symbol=request.symbol,
            timeframe=request.timeframe,
            start=start,
            end=end,
            initial_balance=request.initial_balance,
            risk_percent=request.risk_percent,
            strategy_name=request.strategy,
            params=request.params,
        )
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/backtest/results")
def backtest_results(limit: int = Query(default=25, ge=1, le=200)) -> dict[str, Any]:
    return {"results": get_state().db.list_backtests(limit=limit), "disclaimer": DISCLAIMER}


@router.get("/backtest/results/{backtest_id}")
def backtest_result(backtest_id: int) -> dict[str, Any]:
    result = get_state().db.get_backtest(backtest_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Backtest {backtest_id} not found")
    result["disclaimer"] = DISCLAIMER
    return result


@router.delete("/backtest/results/{backtest_id}")
def delete_backtest(backtest_id: int) -> dict[str, Any]:
    get_state().db.delete_backtest(backtest_id)
    return {"deleted": backtest_id}


# ----------------------------------------------------------------------
# risk & logs
# ----------------------------------------------------------------------
@router.get("/risk")
def risk() -> dict[str, Any]:
    state = get_state()
    try:
        positions = state.engine.positions.list_positions(only_bot=True)
    except MT5Error:
        positions = []
    return state.engine.risk.summary(open_positions=len(positions))


@router.get("/logs")
def logs(
    limit: int = Query(default=200, ge=1, le=2000),
    category: str | None = None,
    source: str = Query(default="db", pattern="^(db|memory)$"),
) -> dict[str, Any]:
    state = get_state()
    if source == "memory":
        records = recent_logs(limit=limit, categories=[category] if category and category != "ALL" else None)
        return {"logs": list(reversed(records)), "categories": list(CATEGORIES)}
    return {
        "logs": state.db.recent_logs(limit=limit, category=category),
        "categories": list(CATEGORIES),
    }
