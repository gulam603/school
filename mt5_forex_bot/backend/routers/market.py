"""Market data routes: quotes, candles, chart payloads, symbol specs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app_state import get_state
from backend.config import TIMEFRAMES
from backend.indicators import add_indicators, latest_values
from backend.mt5_errors import MT5Error

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/watch")
def market_watch() -> dict[str, Any]:
    state = get_state()
    try:
        rows = state.market.market_watch(
            state.config.traded_symbols, state.config.symbol_prefix, state.config.symbol_suffix
        )
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc
    return {"symbols": rows, "timeframes": list(TIMEFRAMES)}


@router.get("/tick/{symbol}")
def tick(symbol: str) -> dict[str, Any]:
    state = get_state()
    try:
        broker_symbol = state.market.resolve(
            symbol, state.config.symbol_prefix, state.config.symbol_suffix
        )
        return state.market.get_tick(broker_symbol)
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc


@router.get("/spec/{symbol}")
def spec(symbol: str) -> dict[str, Any]:
    state = get_state()
    try:
        broker_symbol = state.market.resolve(
            symbol, state.config.symbol_prefix, state.config.symbol_suffix
        )
        return state.market.symbol_spec(broker_symbol).to_dict()
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc


@router.get("/candles/{symbol}")
def candles(
    symbol: str,
    timeframe: str = Query(default="H1"),
    count: int = Query(default=300, ge=10, le=5000),
) -> dict[str, Any]:
    """Candles plus the EMA overlays the chart draws."""
    state = get_state()
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"Unsupported timeframe '{timeframe}'")
    try:
        return state.market.chart_payload(
            symbol,
            timeframe,
            count,
            state.config.symbol_prefix,
            state.config.symbol_suffix,
            state.config.ema_fast,
            state.config.ema_slow,
        )
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc


@router.get("/indicators/{symbol}")
def indicators(
    symbol: str,
    timeframe: str = Query(default="H1"),
    count: int = Query(default=300, ge=50, le=5000),
) -> dict[str, Any]:
    """Latest values of the full indicator set (EMA, SMA, RSI, MACD, ATR, BB)."""
    state = get_state()
    try:
        broker_symbol = state.market.resolve(
            symbol, state.config.symbol_prefix, state.config.symbol_suffix
        )
        frame = state.market.get_rates(broker_symbol, timeframe.upper(), count)
    except MT5Error as exc:
        raise HTTPException(status_code=503, detail=exc.as_dict()) from exc
    if frame.empty:
        raise HTTPException(status_code=404, detail=f"No candles for {symbol} {timeframe}")

    enriched = add_indicators(
        state.market.closed_bars(frame),
        ema_fast=state.config.ema_fast,
        ema_slow=state.config.ema_slow,
        rsi_period=state.config.rsi_period,
        atr_period=state.config.atr_period,
    )
    return {
        "symbol": symbol.upper(),
        "broker_symbol": broker_symbol,
        "timeframe": timeframe.upper(),
        "bar_time": str(enriched["time"].iloc[-1]) if not enriched.empty else None,
        "values": latest_values(enriched),
    }
