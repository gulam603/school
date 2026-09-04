"""FastAPI application - the local backend for the MT5 dashboard.

Run it with::

    python -m backend.main          # or: uvicorn backend.main:app --reload

It binds to 127.0.0.1 by default: this is a personal tool that talks to the MT5
terminal on the same machine and must not be exposed to a network.

The API surface is documented at http://127.0.0.1:8000/docs while the server is
running. The dashboard connects to ``/ws`` for live updates and falls back to
polling ``/api/status`` if the socket drops.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import uvicorn
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app_state import get_state
from backend.config import ROOT_DIR, settings
from backend.logger import configure_logging, get_logger
from backend.mt5_connection import mt5_available
from backend.mt5_errors import MT5Error
from backend.routers import analysis, market, system, trading

configure_logging()
log = get_logger("api")

FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


class WebSocketHub:
    """Fans engine/log events out to every connected dashboard."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        log.debug(f"Dashboard connected ({len(self._clients)} open)")

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_text(payload)
            except Exception:
                dead.append(client)
        if dead:
            async with self._lock:
                for client in dead:
                    self._clients.discard(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)


hub = WebSocketHub()


async def _event_pump() -> None:
    """Drain events produced by background threads onto the WebSocket."""
    state = get_state()
    loop = asyncio.get_running_loop()
    while True:
        try:
            event = await loop.run_in_executor(None, _next_event, state.events)
            if event is not None:
                await hub.broadcast(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("event pump error", exc_info=True)
            await asyncio.sleep(0.5)


def _next_event(events: "queue.Queue[dict[str, Any]]") -> dict[str, Any] | None:
    try:
        return events.get(timeout=1.0)
    except queue.Empty:
        return None


async def _snapshot_pump() -> None:
    """Push a full snapshot once a second so the dashboard stays live."""
    state = get_state()
    while True:
        try:
            await asyncio.sleep(1.0)
            if hub.client_count == 0:
                continue
            snapshot = await asyncio.to_thread(state.snapshot)
            await hub.broadcast({"type": "snapshot", "payload": snapshot})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("snapshot pump error", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = get_state()
    log.info("=" * 68)
    log.info(f"MT5 Forex Bot backend starting - mode: {'DEMO' if settings.is_demo_mode else 'LIVE'}")
    if not settings.is_demo_mode:
        log.warning("LIVE TRADING IS UNLOCKED - real money orders are possible")
    if not mt5_available():
        log.warning(
            "The MetaTrader5 package is not importable on this machine. The dashboard "
            "will start, but trading requires Windows with MetaTrader 5 installed."
        )
    else:
        # Best effort: attach to a terminal that is already running.
        try:
            state.conn.connect()
            state.resolver.refresh()
        except MT5Error as exc:
            log.warning(f"Auto-connect skipped: {exc.message}")

    tasks = [asyncio.create_task(_event_pump()), asyncio.create_task(_snapshot_pump())]
    log.info(f"API ready on http://{settings.api_host}:{settings.api_port}")
    log.info("=" * 68)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        state.shutdown()
        log.info("Backend stopped")


app = FastAPI(
    title="MT5 Forex Bot",
    description=(
        "Local API for the personal MetaTrader 5 demo trading terminal. "
        "Runs on localhost only; MT5 and Telegram credentials never leave the backend."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")
api.include_router(system.router)
api.include_router(market.router)
api.include_router(trading.router)
api.include_router(analysis.router)
app.include_router(api)


@app.exception_handler(MT5Error)
async def mt5_exception_handler(request: Any, exc: MT5Error) -> JSONResponse:
    """Any MT5 failure that escapes a router becomes a clean 503."""
    log.error(f"Unhandled MT5 error on {getattr(request, 'url', '')}: {exc.message}")
    return JSONResponse(status_code=503, content={"detail": exc.as_dict()})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Live dashboard feed: snapshots, log lines, signals and trade events."""
    await hub.connect(websocket)
    state = get_state()
    try:
        await websocket.send_text(
            json.dumps({"type": "snapshot", "payload": state.snapshot()}, default=str)
        )
        while True:
            # The client only sends keepalives; receiving detects disconnects.
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("websocket error", exc_info=True)
    finally:
        await hub.disconnect(websocket)


# ----------------------------------------------------------------------
# built frontend (start.bat serves the dashboard from Vite in development)
# ----------------------------------------------------------------------
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": "MT5 Forex Bot API",
            "docs": "/docs",
            "dashboard": "run `npm run dev` in frontend/, or `npm run build` to serve it here",
        }


def open_browser_later(url: str, delay: float = 2.0) -> None:
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def main() -> None:
    url = f"http://{settings.api_host}:{settings.api_port}"
    if FRONTEND_DIST.is_dir():
        open_browser_later(url)
    uvicorn.run(
        "backend.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
