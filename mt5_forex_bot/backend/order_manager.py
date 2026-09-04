"""Order execution against the MT5 terminal.

Every order the bot sends goes through this module, which is also the single
place where the demo/live safety gate lives:

* the account's ``trade_mode`` is read from the terminal itself,
* if it is a REAL money account, orders are refused unless the operator has
  unlocked live trading in ``.env`` (mode + flag + confirmation phrase),
* every request and every reply is written to the ``orders`` table.

Transient failures (requote, price changed, timeout, busy server) are retried
with a fresh price; permanent ones (invalid volume, market closed, no money)
raise a typed error immediately.  Nothing here raises past the caller without
being logged first - a single rejected order must never stop the bot.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.config import BotConfig, settings
from backend.database import Database, get_db
from backend.logger import get_logger
from backend.market_data import MarketData, SymbolSpec, get_market_data
from backend.mt5_connection import MT5Connection, get_connection, load_mt5
from backend.mt5_errors import (
    InsufficientMarginError,
    InvalidVolumeError,
    LiveTradingBlockedError,
    MarketClosedError,
    MT5Error,
    MT5NotConnectedError,
    OrderRejectedError,
)
from backend.notifications import Notifier, get_notifier

log = get_logger("orders")

#: MT5 trade server return codes -> (human text, retryable?)
RETCODES: dict[int, tuple[str, bool]] = {
    10004: ("Requote", True),
    10006: ("Request rejected", False),
    10007: ("Request cancelled by trader", False),
    10008: ("Order placed", False),
    10009: ("Request completed", False),
    10010: ("Only part of the request was completed", False),
    10011: ("Request processing error", True),
    10012: ("Request timed out", True),
    10013: ("Invalid request", False),
    10014: ("Invalid volume", False),
    10015: ("Invalid price", True),
    10016: ("Invalid stops (SL/TP too close to price)", False),
    10017: ("Trading is disabled", False),
    10018: ("Market is closed", False),
    10019: ("Not enough money", False),
    10020: ("Price changed", True),
    10021: ("No quotes to process the request", True),
    10022: ("Invalid order expiration", False),
    10023: ("Order state changed", True),
    10024: ("Too many requests", True),
    10025: ("No changes in the request", False),
    10026: ("Autotrading disabled by the server", False),
    10027: ("Autotrading disabled by the client terminal", False),
    10028: ("Request locked for processing", True),
    10029: ("Order or position frozen", False),
    10030: ("Unsupported filling mode", False),
    10031: ("No connection to the trade server", True),
    10032: ("Operation allowed on live accounts only", False),
    10033: ("Pending order limit reached", False),
    10034: ("Volume limit for this symbol reached", False),
    10035: ("Invalid or prohibited order type", False),
    10036: ("Position already closed", False),
    10038: ("Close volume exceeds the position volume", False),
    10039: ("A close order already exists for this position", False),
    10040: ("Maximum number of open positions reached", False),
    10041: ("Close request rejected, order cancelled", False),
    10042: ("Only long positions are allowed", False),
    10043: ("Only short positions are allowed", False),
    10044: ("Only position closing is allowed", False),
    10045: ("Position closing is allowed by FIFO rule only", False),
    10046: ("Opposite positions on a single symbol are disabled", False),
}

RETRYABLE_RETCODES = {code for code, (_, retry) in RETCODES.items() if retry}


def retcode_text(retcode: int | None) -> str:
    if retcode is None:
        return "no reply from the trade server"
    return RETCODES.get(int(retcode), (f"Unknown return code {retcode}", False))[0]


@dataclass
class TradeResult:
    """Outcome of one order request."""

    success: bool
    action: str
    symbol: str
    retcode: int | None = None
    message: str = ""
    ticket: int | None = None
    volume: float = 0.0
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    request: dict[str, Any] = field(default_factory=dict)
    raw_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "symbol": self.symbol,
            "retcode": self.retcode,
            "retcode_text": retcode_text(self.retcode),
            "message": self.message,
            "ticket": self.ticket,
            "volume": self.volume,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
        }


class OrderManager:
    """Sends market orders, SL/TP modifications and closes to MT5."""

    MAX_ATTEMPTS = 3

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
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # safety gate
    # ------------------------------------------------------------------
    def assert_trading_allowed(self) -> dict[str, Any]:
        """Refuse to trade a real-money account unless explicitly unlocked."""
        account = self.conn.account_info()
        if not account["is_demo"] and not settings.live_trading_unlocked:
            raise LiveTradingBlockedError(
                f"Account {account['login']} is a REAL MONEY account and live trading is "
                "not unlocked. The bot only sends orders to demo accounts unless "
                "TRADING_MODE=live, ALLOW_LIVE_TRADING=true and the confirmation phrase "
                "are all set in .env."
            )
        if not account["trade_allowed"]:
            raise OrderRejectedError(
                "Trading is disabled for this account (check the terminal's "
                "'Algo Trading' button and the account's trade permissions)"
            )
        if settings.mt5_require_algo_trading:
            terminal = self.conn.terminal_info()
            if not terminal["trade_allowed"]:
                raise OrderRejectedError(
                    "Algo Trading is switched off in the MetaTrader 5 terminal. "
                    "Press the 'Algo Trading' button in MT5 and try again."
                )
        return account

    @property
    def mode(self) -> str:
        return "live" if settings.live_trading_unlocked else "demo"

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _filling_modes(self, broker_symbol: str) -> list[int]:
        """Filling modes to try, best supported one first."""
        mt5 = load_mt5()
        info = self.conn.symbol_info(broker_symbol)
        mask = int(getattr(info, "filling_mode", 0)) if info is not None else 0
        modes: list[int] = []
        # SYMBOL_FILLING_FOK = 1, SYMBOL_FILLING_IOC = 2
        if mask & 2:
            modes.append(mt5.ORDER_FILLING_IOC)
        if mask & 1:
            modes.append(mt5.ORDER_FILLING_FOK)
        for fallback in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            if fallback not in modes:
                modes.append(fallback)
        return modes

    def _record_order(
        self,
        action: str,
        symbol: str,
        request: dict[str, Any],
        result: Any,
        success: bool,
        error_message: str | None = None,
        order_type: str = "",
    ) -> None:
        result_dict = result._asdict() if hasattr(result, "_asdict") else (result or {})
        retcode = int(result_dict.get("retcode")) if result_dict.get("retcode") is not None else None
        self.db.insert_order(
            {
                "ticket": result_dict.get("order") or result_dict.get("deal") or request.get("position"),
                "symbol": symbol,
                "order_type": order_type,
                "action": action,
                "volume": request.get("volume"),
                "price": result_dict.get("price") or request.get("price"),
                "stop_loss": request.get("sl"),
                "take_profit": request.get("tp"),
                "retcode": retcode,
                "retcode_text": retcode_text(retcode),
                "comment": result_dict.get("comment") or request.get("comment"),
                "request": {k: v for k, v in request.items()},
                "result": {k: v for k, v in result_dict.items()},
                "success": success,
                "error_message": error_message,
                "mode": self.mode,
            }
        )

    def _raise_for_retcode(self, retcode: int, comment: str, symbol: str) -> None:
        text = retcode_text(retcode)
        detail = f"{text} (retcode {retcode})" + (f": {comment}" if comment else "")
        if retcode == 10014:
            raise InvalidVolumeError(detail, retcode)
        if retcode == 10019:
            raise InsufficientMarginError(detail, retcode)
        if retcode == 10018:
            raise MarketClosedError(detail, retcode)
        raise OrderRejectedError(f"{symbol}: {detail}", retcode, text)

    # ------------------------------------------------------------------
    # open
    # ------------------------------------------------------------------
    def open_position(
        self,
        canonical_symbol: str,
        broker_symbol: str,
        direction: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
        strategy: str = "",
        signal_reason: str = "",
        timeframe: str = "",
        risk_amount: float = 0.0,
        risk_percent: float = 0.0,
    ) -> TradeResult:
        """Send a market order and persist the resulting trade."""
        with self._lock:
            direction = direction.upper()
            account = self.assert_trading_allowed()
            mt5 = load_mt5()
            spec = self.market.symbol_spec(broker_symbol)

            volume = spec.normalize_volume(volume)
            if volume < spec.volume_min:
                raise InvalidVolumeError(
                    f"Volume {volume} is below the broker minimum {spec.volume_min} for {broker_symbol}"
                )
            if not self.market.is_market_open(broker_symbol):
                raise MarketClosedError(f"Market for {broker_symbol} is closed")

            order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
            filling_modes = self._filling_modes(broker_symbol)
            filling_index = 0          # only advances when the server rejects the mode
            last_error: MT5Error | None = None

            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                tick = self.market.get_tick(broker_symbol)
                price = float(tick["ask"] if direction == "BUY" else tick["bid"])
                filling = filling_modes[min(filling_index, len(filling_modes) - 1)]

                request: dict[str, Any] = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": broker_symbol,
                    "volume": float(volume),
                    "type": order_type,
                    "price": price,
                    "deviation": int(self.config.deviation_points),
                    "magic": int(self.config.magic_number),
                    "comment": (comment or f"{strategy or 'bot'}")[:31],
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                if stop_loss:
                    request["sl"] = spec.normalize_price(stop_loss)
                if take_profit:
                    request["tp"] = spec.normalize_price(take_profit)

                try:
                    result = self.conn.order_send(request)
                except MT5NotConnectedError as exc:
                    self._record_order("OPEN", broker_symbol, request, None, False, exc.message, direction)
                    raise

                if result is None:
                    code, description = self.conn.last_error()
                    message = f"order_send returned nothing: {description} (code {code})"
                    self._record_order("OPEN", broker_symbol, request, None, False, message, direction)
                    last_error = OrderRejectedError(message, code)
                    log.warning(f"{broker_symbol} {direction} attempt {attempt}: {message}")
                    time.sleep(0.5 * attempt)
                    continue

                retcode = int(result.retcode)
                success = retcode in (mt5.TRADE_RETCODE_DONE, 10008, 10010)
                self._record_order(
                    "OPEN", broker_symbol, request, result, success,
                    None if success else retcode_text(retcode), direction,
                )

                if success:
                    ticket = int(getattr(result, "order", 0) or 0)
                    deal_price = float(getattr(result, "price", price) or price)
                    filled = float(getattr(result, "volume", volume) or volume)
                    position_ticket = self._find_position_ticket(broker_symbol, ticket)

                    log.trade(
                        f"{'DEMO' if self.mode == 'demo' else 'LIVE'} ORDER EXECUTED "
                        f"#{position_ticket or ticket} {canonical_symbol} {direction} "
                        f"{filled} @ {deal_price}",
                        symbol=canonical_symbol,
                    )
                    if stop_loss:
                        log.trade(f"SL: {spec.normalize_price(stop_loss)}", symbol=canonical_symbol)
                    if take_profit:
                        log.trade(f"TP: {spec.normalize_price(take_profit)}", symbol=canonical_symbol)

                    self.db.insert_trade(
                        {
                            "ticket": position_ticket or ticket,
                            "symbol": canonical_symbol,
                            "broker_symbol": broker_symbol,
                            "direction": direction,
                            "volume": filled,
                            "entry_price": deal_price,
                            "stop_loss": spec.normalize_price(stop_loss) if stop_loss else None,
                            "take_profit": spec.normalize_price(take_profit) if take_profit else None,
                            "initial_sl": spec.normalize_price(stop_loss) if stop_loss else None,
                            "initial_tp": spec.normalize_price(take_profit) if take_profit else None,
                            "risk_amount": risk_amount,
                            "risk_percent": risk_percent,
                            "strategy": strategy,
                            "signal_reason": signal_reason,
                            "timeframe": timeframe,
                            "magic": int(self.config.magic_number),
                            "opened_at": datetime.now(timezone.utc).isoformat(),
                            "status": "open",
                            "mode": self.mode,
                            "comment": comment,
                        }
                    )
                    self.notifier.trade_opened(
                        canonical_symbol, direction, filled, deal_price,
                        stop_loss, take_profit, position_ticket or ticket,
                    )
                    return TradeResult(
                        success=True, action="OPEN", symbol=canonical_symbol, retcode=retcode,
                        message="Order executed", ticket=position_ticket or ticket, volume=filled,
                        price=deal_price, stop_loss=stop_loss, take_profit=take_profit,
                        request=request, raw_result=result._asdict(),
                    )

                comment_text = str(getattr(result, "comment", "") or "")
                if retcode in RETRYABLE_RETCODES and attempt < self.MAX_ATTEMPTS:
                    log.warning(
                        f"{broker_symbol} {direction} attempt {attempt} failed: "
                        f"{retcode_text(retcode)} - retrying with a fresh price",
                        symbol=canonical_symbol,
                    )
                    self.market.invalidate(broker_symbol)
                    time.sleep(0.4 * attempt)
                    continue
                if retcode == 10030 and filling_index + 1 < len(filling_modes):
                    filling_index += 1
                    log.warning(
                        f"{broker_symbol}: filling mode rejected, trying the next one",
                        symbol=canonical_symbol,
                    )
                    continue

                self._raise_for_retcode(retcode, comment_text, canonical_symbol)

            if last_error:
                raise last_error
            raise OrderRejectedError(
                f"{canonical_symbol}: order failed after {self.MAX_ATTEMPTS} attempts"
            )

    def _find_position_ticket(self, broker_symbol: str, order_ticket: int) -> int | None:
        """Map the order ticket returned by order_send onto the position ticket."""
        try:
            positions = self.conn.positions_get(symbol=broker_symbol)
        except MT5Error:
            return order_ticket or None
        for position in positions:
            if int(getattr(position, "ticket", 0)) == order_ticket:
                return order_ticket
        # Netting accounts merge into one position per symbol; take the newest.
        candidates = [
            p for p in positions if int(getattr(p, "magic", 0)) == int(self.config.magic_number)
        ]
        if candidates:
            newest = max(candidates, key=lambda p: int(getattr(p, "time", 0)))
            return int(newest.ticket)
        return order_ticket or None

    # ------------------------------------------------------------------
    # modify
    # ------------------------------------------------------------------
    def modify_position(
        self, ticket: int, stop_loss: float | None = None, take_profit: float | None = None
    ) -> TradeResult:
        """Change SL/TP of an open position."""
        with self._lock:
            self.assert_trading_allowed()
            mt5 = load_mt5()
            positions = self.conn.positions_get(ticket=ticket)
            if not positions:
                raise OrderRejectedError(f"Position #{ticket} not found")
            position = positions[0]
            broker_symbol = str(position.symbol)
            spec = self.market.symbol_spec(broker_symbol)

            new_sl = spec.normalize_price(stop_loss) if stop_loss else float(position.sl)
            new_tp = spec.normalize_price(take_profit) if take_profit else float(position.tp)
            if abs(new_sl - float(position.sl)) < spec.point / 2 and abs(new_tp - float(position.tp)) < spec.point / 2:
                return TradeResult(
                    success=True, action="MODIFY", symbol=broker_symbol, ticket=ticket,
                    message="No change required", stop_loss=new_sl, take_profit=new_tp,
                )

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": int(ticket),
                "symbol": broker_symbol,
                "sl": new_sl,
                "tp": new_tp,
                "magic": int(self.config.magic_number),
            }
            result = self.conn.order_send(request)
            if result is None:
                code, description = self.conn.last_error()
                message = f"Modify failed: {description} (code {code})"
                self._record_order("MODIFY", broker_symbol, request, None, False, message)
                raise OrderRejectedError(message, code)

            retcode = int(result.retcode)
            success = retcode in (mt5.TRADE_RETCODE_DONE, 10025)
            self._record_order(
                "MODIFY", broker_symbol, request, result, success,
                None if success else retcode_text(retcode),
            )
            if not success:
                self._raise_for_retcode(retcode, str(getattr(result, "comment", "")), broker_symbol)

            self.db.update_trade_levels(int(ticket), new_sl, new_tp)
            log.trade(f"Position #{ticket} updated - SL: {new_sl} TP: {new_tp}")
            return TradeResult(
                success=True, action="MODIFY", symbol=broker_symbol, retcode=retcode,
                message="SL/TP updated", ticket=int(ticket), stop_loss=new_sl, take_profit=new_tp,
                request=request, raw_result=result._asdict(),
            )

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------
    def close_position(
        self, ticket: int, volume: float | None = None, reason: str = "manual"
    ) -> TradeResult:
        """Close a position completely, or partially when ``volume`` is given."""
        with self._lock:
            self.assert_trading_allowed()
            mt5 = load_mt5()
            positions = self.conn.positions_get(ticket=ticket)
            if not positions:
                raise OrderRejectedError(f"Position #{ticket} not found (already closed?)", 10036)
            position = positions[0]
            broker_symbol = str(position.symbol)
            spec = self.market.symbol_spec(broker_symbol)
            position_volume = float(position.volume)
            close_volume = spec.normalize_volume(volume) if volume else position_volume
            close_volume = min(close_volume, position_volume)
            is_partial = close_volume < position_volume - 1e-9

            is_buy = int(position.type) == mt5.POSITION_TYPE_BUY
            close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
            filling_modes = self._filling_modes(broker_symbol)
            filling_index = 0
            canonical = self.market.to_canonical(broker_symbol)

            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                tick = self.market.get_tick(broker_symbol)
                price = float(tick["bid"] if is_buy else tick["ask"])
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": broker_symbol,
                    "volume": float(close_volume),
                    "type": close_type,
                    "position": int(ticket),
                    "price": price,
                    "deviation": int(self.config.deviation_points),
                    "magic": int(self.config.magic_number),
                    "comment": f"close:{reason}"[:31],
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling_modes[min(filling_index, len(filling_modes) - 1)],
                }
                result = self.conn.order_send(request)
                if result is None:
                    code, description = self.conn.last_error()
                    message = f"Close failed: {description} (code {code})"
                    self._record_order("CLOSE", broker_symbol, request, None, False, message)
                    if attempt == self.MAX_ATTEMPTS:
                        raise OrderRejectedError(message, code)
                    time.sleep(0.4 * attempt)
                    continue

                retcode = int(result.retcode)
                success = retcode in (mt5.TRADE_RETCODE_DONE, 10008, 10010)
                self._record_order(
                    "PARTIAL" if is_partial else "CLOSE", broker_symbol, request, result, success,
                    None if success else retcode_text(retcode),
                )
                if success:
                    profit = float(getattr(position, "profit", 0.0))
                    if is_partial:
                        self.db.update_trade_volume(int(ticket), position_volume - close_volume)
                        log.trade(
                            f"Partial close #{ticket} {canonical} {close_volume}/{position_volume} "
                            f"@ {price} ({reason})",
                            symbol=canonical,
                        )
                    else:
                        self.db.close_trade(
                            int(ticket), price, profit, reason,
                            swap=float(getattr(position, "swap", 0.0)),
                        )
                        log.trade(
                            f"Position #{ticket} {canonical} closed @ {price} "
                            f"P/L {profit:+.2f} ({reason})",
                            symbol=canonical,
                        )
                        self.notifier.trade_closed(
                            canonical, "BUY" if is_buy else "SELL", close_volume, profit, reason, int(ticket)
                        )
                    return TradeResult(
                        success=True, action="PARTIAL" if is_partial else "CLOSE",
                        symbol=canonical, retcode=retcode, ticket=int(ticket),
                        volume=close_volume, price=price,
                        message="Partially closed" if is_partial else "Position closed",
                        request=request, raw_result=result._asdict(),
                    )

                if retcode in RETRYABLE_RETCODES and attempt < self.MAX_ATTEMPTS:
                    log.warning(
                        f"Close #{ticket} attempt {attempt}: {retcode_text(retcode)} - retrying"
                    )
                    self.market.invalidate(broker_symbol)
                    time.sleep(0.4 * attempt)
                    continue
                if retcode == 10030 and filling_index + 1 < len(filling_modes):
                    filling_index += 1
                    log.warning(f"Close #{ticket}: filling mode rejected, trying the next one")
                    continue
                self._raise_for_retcode(retcode, str(getattr(result, "comment", "")), canonical)

            raise OrderRejectedError(f"Could not close #{ticket} after {self.MAX_ATTEMPTS} attempts")

    def close_all(self, only_bot_positions: bool = False, reason: str = "close_all") -> dict[str, Any]:
        """Emergency close. Never stops at the first failure."""
        with self._lock:
            try:
                positions = self.conn.positions_get()
            except MT5Error as exc:
                return {"closed": 0, "failed": 0, "errors": [exc.message]}

            closed = failed = 0
            errors: list[str] = []
            for position in positions:
                ticket = int(position.ticket)
                if only_bot_positions and int(getattr(position, "magic", 0)) != int(self.config.magic_number):
                    continue
                try:
                    self.close_position(ticket, reason=reason)
                    closed += 1
                except MT5Error as exc:
                    failed += 1
                    errors.append(f"#{ticket}: {exc.message}")
                    log.error(f"Failed to close #{ticket}: {exc.message}")
            log.trade(f"Close-all finished: {closed} closed, {failed} failed ({reason})")
            return {"closed": closed, "failed": failed, "errors": errors}
