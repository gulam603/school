"""The bot: one background thread that turns candles into managed positions.

Each tick of the loop:

1. make sure the MT5 terminal is connected (reconnecting with backoff if not),
2. run the position manager over open positions (break-even, trailing, ...),
3. for every traded symbol, pull candles, drop the forming candle, ask the
   strategy for a signal on the newly closed bar,
4. hand actionable signals to the risk manager, and only if it says yes, to the
   order manager.

Signals are evaluated once per closed bar per symbol, so a crossover cannot be
traded twice.  Every failure mode is contained: a bad symbol, a rejected order
or an MT5 dropout is logged and the loop continues with the next symbol.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from backend.config import BotConfig, settings
from backend.database import Database, get_db
from backend.logger import get_logger
from backend.market_data import MarketData, get_market_data
from backend.mt5_connection import MT5Connection, get_connection
from backend.mt5_errors import MT5Error, MT5NotAvailableError
from backend.notifications import Notifier, get_notifier
from backend.order_manager import OrderManager
from backend.position_manager import PositionManager
from backend.risk_manager import RiskManager
from strategies import Signal, get_strategy

log = get_logger("engine")


class StrategyEngine:
    """Runs one strategy over a set of symbols on a background thread."""

    def __init__(
        self,
        config: BotConfig,
        connection: MT5Connection | None = None,
        market_data: MarketData | None = None,
        database: Database | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.config = config
        self.conn = connection or get_connection()
        self.market = market_data or get_market_data()
        self.db = database or get_db()
        self.notifier = notifier or get_notifier()

        self.risk = RiskManager(config, self.db, self.market, self.conn)
        self.orders = OrderManager(config, self.conn, self.market, self.db, self.notifier)
        self.positions = PositionManager(
            config, self.orders, self.conn, self.market, self.db, self.notifier
        )

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._running = False
        self._started_at: datetime | None = None
        self._run_id: int | None = None
        self._stop_reason: str = ""
        self._last_tick: datetime | None = None
        self._last_error: str = ""

        self._signals_count = 0
        self._trades_count = 0
        self._last_signal: dict[str, Any] | None = None
        self._last_trade: dict[str, Any] | None = None
        self._current_signals: dict[str, dict[str, Any]] = {}
        self._processed_bars: dict[str, str] = {}
        self._daily_limit_notified = False

        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def add_listener(self, callback: Callable[[str, dict[str, Any]], None]) -> None:
        """Register a callback (event name, payload) for dashboard pushes."""
        self._listeners.append(callback)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, payload)
            except Exception:
                log.debug("engine listener failed", exc_info=True)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> dict[str, Any]:
        """Start the trading loop. Requires a live MT5 connection."""
        with self._lock:
            if self._running:
                return self.status()

            if not self.conn.is_connected():
                self.conn.connect()  # raises a typed MT5Error the API turns into 4xx/5xx

            account = self.orders.assert_trading_allowed()
            strategy = get_strategy(self.config.strategy, self.config.strategy_params())

            self._stop_event.clear()
            self._running = True
            self._started_at = datetime.now(timezone.utc)
            self._stop_reason = ""
            self._last_error = ""
            self._signals_count = 0
            self._trades_count = 0
            self._processed_bars.clear()
            self._daily_limit_notified = False

            self._run_id = self.db.start_strategy_run(
                {
                    "strategy": strategy.name,
                    "symbols": self.config.traded_symbols,
                    "timeframe": self.config.timeframe,
                    "params": self.config.strategy_params(),
                    "mode": self.orders.mode,
                }
            )

            self._thread = threading.Thread(target=self._run, name="strategy-engine", daemon=True)
            self._thread.start()

            mode = "DEMO" if self.orders.mode == "demo" else "LIVE"
            log.info(
                f"Bot started - {strategy.display_name} on "
                f"{', '.join(self.config.traded_symbols)} {self.config.timeframe} "
                f"[{mode}] account {account['login']}"
            )
            self.notifier.bot_started(
                strategy.display_name, self.config.traded_symbols, self.config.timeframe
            )
            self._emit("bot_status", self.status())
            return self.status()

    def stop(self, reason: str = "manual") -> dict[str, Any]:
        """Stop the loop. Open positions are left untouched."""
        with self._lock:
            if not self._running:
                return self.status()
            self._stop_event.set()
            self._running = False
            self._stop_reason = reason

        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10)
        self._thread = None

        if self._run_id:
            self.db.stop_strategy_run(self._run_id, reason, self._signals_count, self._trades_count)
            self._run_id = None

        log.info(f"Bot stopped ({reason}) - {self._signals_count} signals, {self._trades_count} trades")
        self.notifier.bot_stopped(reason)
        self._emit("bot_status", self.status())
        return self.status()

    def emergency_stop(self) -> dict[str, Any]:
        """Panic button: stop the loop *and* flatten every position."""
        log.warning("EMERGENCY STOP requested - stopping the bot and closing all positions")
        result = {"closed": 0, "failed": 0, "errors": []}
        try:
            result = self.orders.close_all(only_bot_positions=False, reason="emergency_stop")
        except MT5Error as exc:
            result["errors"].append(exc.message)
            log.error(f"Emergency close-all failed: {exc.message}")
        self.stop("emergency_stop")
        self.notifier.send(
            "critical_error", "EMERGENCY STOP",
            closed=result.get("closed"), failed=result.get("failed"),
        )
        return {"status": self.status(), "close_all": result}

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def _run(self) -> None:
        interval = max(1, int(self.config.poll_interval_seconds))
        while not self._stop_event.is_set():
            started = time.time()
            try:
                self._tick()
            except MT5NotAvailableError as exc:
                self._last_error = exc.message
                log.error(f"MetaTrader 5 is unavailable: {exc.message}")
                self._running = False
                self._stop_reason = "mt5_unavailable"
                self.notifier.critical_error(exc.message)
                break
            except MT5Error as exc:
                self._last_error = exc.message
                log.error(f"Engine tick failed: {exc.message}")
            except Exception as exc:  # the loop must survive anything
                self._last_error = str(exc)
                log.error(f"Unexpected engine error: {exc}", exc_info=True)
                self.notifier.critical_error(str(exc))

            elapsed = time.time() - started
            self._stop_event.wait(max(0.2, interval - elapsed))

        self._running = False
        self._emit("bot_status", self.status())

    def _tick(self) -> None:
        self._last_tick = datetime.now(timezone.utc)

        if not self.conn.ensure_connected():
            self.notifier.disconnected(self.conn.status().get("last_error", "unknown"))
            self._stop_event.wait(self.conn.reconnect_delay())
            return

        # 1) look after what is already open
        self.positions.manage()
        open_positions = self.positions.list_positions(only_bot=True)
        open_count = len(open_positions)

        # 2) daily loss guard
        account = self.conn.account_info()
        daily = self.risk.daily_status(account)
        if daily.limit_hit:
            if not self._daily_limit_notified:
                self._daily_limit_notified = True
                self.notifier.daily_loss_limit(daily.loss_percent, daily.limit_percent)
                self._emit("risk_alert", daily.to_dict())
            return  # positions stay managed above, but no new trades today
        self._daily_limit_notified = False

        # 3) look for new signals
        strategy = get_strategy(self.config.strategy, self.config.strategy_params())
        open_symbols = {position["symbol"] for position in open_positions}
        for canonical in self.config.traded_symbols:
            if self._stop_event.is_set():
                return
            try:
                opened = self._evaluate_symbol(
                    canonical, strategy, open_count, account, open_symbols
                )
                if opened:
                    # Keep the counters honest for the remaining symbols without
                    # re-reading every position from the terminal each time.
                    open_count += 1
                    open_symbols.add(canonical)
            except MT5Error as exc:
                log.warning(f"{canonical}: {exc.message}", symbol=canonical)
            except Exception as exc:
                log.error(f"{canonical}: unexpected error - {exc}", symbol=canonical, exc_info=True)

    def _evaluate_symbol(
        self,
        canonical: str,
        strategy: Any,
        open_positions: int,
        account: dict[str, Any],
        open_symbols: set[str],
    ) -> bool:
        """Evaluate one symbol. Returns True when a position was opened."""
        broker_symbol = self.market.resolve(canonical, self.config.symbol_prefix, self.config.symbol_suffix)
        bars = max(strategy.required_bars + 50, 200)
        frame = self.market.get_rates(broker_symbol, self.config.timeframe, bars)
        if frame.empty:
            return False

        closed = self.market.closed_bars(frame)
        if closed.empty:
            return False

        bar_time = str(closed["time"].iloc[-1])
        prepared = strategy.prepare(closed)
        signal: Signal = strategy.generate_signal(prepared, canonical, self.config.timeframe)
        self._current_signals[canonical] = signal.to_dict()

        # one decision per closed bar
        if self._processed_bars.get(canonical) == bar_time:
            return False
        self._processed_bars[canonical] = bar_time

        if not signal.is_actionable:
            log.debug(f"{canonical}: {signal.reason}", symbol=canonical)
            return False

        self._signals_count += 1
        self._last_signal = signal.to_dict()
        log.signal(f"{canonical} {signal.direction} - {signal.reason}", symbol=canonical)
        self.notifier.signal(signal.direction, canonical, signal.price, signal.reason)

        signal_id = self.db.insert_signal(
            {
                "symbol": canonical,
                "timeframe": self.config.timeframe,
                "strategy": strategy.name,
                "direction": signal.direction,
                "price": signal.price,
                "reason": signal.reason,
                "indicators": signal.indicators,
                "bar_time": bar_time,
            }
        )
        self._emit("signal", {**signal.to_dict(), "id": signal_id})

        # already in this market? never stack positions on one symbol
        if canonical in open_symbols:
            self.db.mark_signal(signal_id, False, "A position is already open on this symbol")
            log.info(f"{canonical}: signal skipped, a position is already open", symbol=canonical)
            return False

        decision = self.risk.evaluate_trade(
            symbol=canonical,
            broker_symbol=broker_symbol,
            direction=signal.direction,
            entry_price=float(signal.price or 0),
            atr_value=float(signal.atr or 0),
            open_positions=open_positions,
            account=account,
        )
        if not decision.allowed:
            self.db.mark_signal(signal_id, False, decision.reason)
            log.risk(f"{canonical} {signal.direction} rejected: {decision.reason}", symbol=canonical)
            self._emit("signal_rejected", {"symbol": canonical, "reason": decision.reason})
            return False

        log.risk(
            f"Risk calculated: {decision.risk_percent:.2f}% = "
            f"{decision.risk_amount:.2f} {account.get('currency', '')}",
            symbol=canonical,
        )
        log.risk(f"Lot size: {decision.volume}", symbol=canonical)

        try:
            result = self.orders.open_position(
                canonical_symbol=canonical,
                broker_symbol=broker_symbol,
                direction=signal.direction,
                volume=decision.volume,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                comment=f"{strategy.name}",
                strategy=strategy.name,
                signal_reason=signal.reason,
                timeframe=self.config.timeframe,
                risk_amount=decision.risk_amount,
                risk_percent=decision.risk_percent,
            )
        except MT5Error as exc:
            self.db.mark_signal(signal_id, False, exc.message)
            log.error(f"{canonical} order failed: {exc.message}", symbol=canonical)
            self._emit("order_failed", {"symbol": canonical, "error": exc.message})
            return False

        self._trades_count += 1
        self.db.mark_signal(signal_id, True)
        self._last_trade = result.to_dict()
        self._emit("trade_opened", result.to_dict())
        return True

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def uptime_seconds(self) -> int:
        if not self._started_at or not self._running:
            return 0
        return int((datetime.now(timezone.utc) - self._started_at).total_seconds())

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "uptime_seconds": self.uptime_seconds(),
            "strategy": self.config.strategy,
            "symbols": list(self.config.traded_symbols),
            "timeframe": self.config.timeframe,
            "mode": self.orders.mode,
            "demo_mode": settings.is_demo_mode,
            "signals": self._signals_count,
            "trades": self._trades_count,
            "last_signal": self._last_signal,
            "last_trade": self._last_trade,
            "current_signals": self._current_signals,
            "last_tick": self._last_tick.isoformat() if self._last_tick else None,
            "last_error": self._last_error,
            "stop_reason": self._stop_reason,
            "daily_limit_blocked": self._daily_limit_notified,
        }
