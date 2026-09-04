"""SQLite persistence layer.

A single database file (``data/mt5_bot.db``) holds configuration, every signal
the strategy produced, every order the bot sent, the resulting trades, the
per-day performance roll-up, the log stream and backtest results.

The connection is shared across threads (the FastAPI event loop, the strategy
engine thread and the position manager all write to it) and guarded by a lock,
with WAL journalling so readers are never blocked by the writer.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from backend.config import settings
from backend.logger import get_logger

log = get_logger("database")

SCHEMA = """
-- Key/value store for the runtime BotConfig and other app state.
CREATE TABLE IF NOT EXISTS app_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- One row per position the bot opened, updated when it closes.
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket          INTEGER UNIQUE,
    symbol          TEXT NOT NULL,
    broker_symbol   TEXT,
    direction       TEXT NOT NULL,              -- BUY / SELL
    volume          REAL NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    stop_loss       REAL,
    take_profit     REAL,
    initial_sl      REAL,
    initial_tp      REAL,
    risk_amount     REAL,
    risk_percent    REAL,
    strategy        TEXT,
    signal_reason   TEXT,
    timeframe       TEXT,
    magic           INTEGER,
    opened_at       TEXT,
    closed_at       TEXT,
    profit          REAL DEFAULT 0,
    commission      REAL DEFAULT 0,
    swap            REAL DEFAULT 0,
    close_reason    TEXT,
    status          TEXT NOT NULL DEFAULT 'open', -- open / closed / error
    mode            TEXT NOT NULL DEFAULT 'demo', -- demo / live
    error_message   TEXT,
    comment         TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_status  ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_opened  ON trades(opened_at);
CREATE INDEX IF NOT EXISTS idx_trades_symbol  ON trades(symbol);

-- Snapshot of live positions (refreshed from MT5, used for the dashboard and
-- for restoring position-management state after a bot restart).
CREATE TABLE IF NOT EXISTS positions (
    ticket          INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    volume          REAL NOT NULL,
    entry_price     REAL,
    current_price   REAL,
    stop_loss       REAL,
    take_profit     REAL,
    initial_sl      REAL,
    profit          REAL,
    swap            REAL,
    opened_at       TEXT,
    magic           INTEGER,
    strategy        TEXT,
    break_even_done INTEGER DEFAULT 0,
    partial_done    INTEGER DEFAULT 0,
    updated_at      TEXT
);

-- Every order request the bot sent to MT5 plus the terminal's answer.
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket          INTEGER,
    symbol          TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    action          TEXT NOT NULL,              -- OPEN / CLOSE / MODIFY / PARTIAL
    volume          REAL,
    price           REAL,
    stop_loss       REAL,
    take_profit     REAL,
    retcode         INTEGER,
    retcode_text    TEXT,
    comment         TEXT,
    request_json    TEXT,
    result_json     TEXT,
    success         INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    mode            TEXT NOT NULL DEFAULT 'demo',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

-- Signals produced by a strategy, whether or not they were traded.
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    direction       TEXT NOT NULL,              -- BUY / SELL / NONE
    price           REAL,
    reason          TEXT,
    indicators_json TEXT,
    executed        INTEGER NOT NULL DEFAULT 0,
    rejected_reason TEXT,
    bar_time        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

-- One row per start/stop cycle of the strategy engine.
CREATE TABLE IF NOT EXISTS strategy_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy        TEXT NOT NULL,
    symbols         TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    params_json     TEXT,
    mode            TEXT NOT NULL DEFAULT 'demo',
    started_at      TEXT NOT NULL,
    stopped_at      TEXT,
    stop_reason     TEXT,
    signals_count   INTEGER DEFAULT 0,
    trades_count    INTEGER DEFAULT 0
);

-- Structured log stream (mirrors logs/mt5_bot.log, queryable by the dashboard).
CREATE TABLE IF NOT EXISTS bot_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    category    TEXT NOT NULL,
    message     TEXT NOT NULL,
    symbol      TEXT,
    source      TEXT,
    details     TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_time     ON bot_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_category ON bot_logs(category);

-- Daily roll-up used by the daily-loss guard and the performance page.
CREATE TABLE IF NOT EXISTS daily_performance (
    day             TEXT PRIMARY KEY,
    starting_balance REAL,
    ending_balance  REAL,
    realized_pnl    REAL DEFAULT 0,
    trades          INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    max_drawdown    REAL DEFAULT 0,
    daily_loss_hit  INTEGER DEFAULT 0,
    updated_at      TEXT
);

-- Saved backtest runs (summary + equity curve + trade list as JSON).
CREATE TABLE IF NOT EXISTS backtest_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    start_date      TEXT,
    end_date        TEXT,
    initial_balance REAL,
    final_balance   REAL,
    risk_percent    REAL,
    total_trades    INTEGER,
    winning_trades  INTEGER,
    losing_trades   INTEGER,
    win_rate        REAL,
    net_profit      REAL,
    max_drawdown    REAL,
    profit_factor   REAL,
    average_win     REAL,
    average_loss    REAL,
    largest_win     REAL,
    largest_loss    REAL,
    expectancy      REAL,
    params_json     TEXT,
    equity_json     TEXT,
    trades_json     TEXT,
    created_at      TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper. All timestamps are ISO-8601 UTC strings."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else settings.db_file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------
    # low level helpers
    # ------------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    def save_config(self, key: str, value: dict[str, Any] | list[Any]) -> None:
        self.execute(
            "INSERT INTO app_config(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), _utcnow()),
        )

    def load_config(self, key: str) -> Any | None:
        row = self.query_one("SELECT value FROM app_config WHERE key=?", (key,))
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            log.warning(f"Corrupt config value for '{key}', ignoring it")
            return None

    # ------------------------------------------------------------------
    # trades
    # ------------------------------------------------------------------
    def insert_trade(self, trade: dict[str, Any]) -> int:
        columns = [
            "ticket", "symbol", "broker_symbol", "direction", "volume", "entry_price",
            "stop_loss", "take_profit", "initial_sl", "initial_tp", "risk_amount",
            "risk_percent", "strategy", "signal_reason", "timeframe", "magic",
            "opened_at", "status", "mode", "comment",
        ]
        values = [trade.get(c) for c in columns]
        placeholders = ",".join("?" * len(columns))
        cur = self.execute(
            f"INSERT OR REPLACE INTO trades({','.join(columns)}) VALUES({placeholders})",
            values,
        )
        return int(cur.lastrowid or 0)

    def close_trade(
        self,
        ticket: int,
        exit_price: float | None,
        profit: float,
        close_reason: str,
        commission: float = 0.0,
        swap: float = 0.0,
        closed_at: str | None = None,
    ) -> None:
        self.execute(
            "UPDATE trades SET exit_price=?, profit=?, commission=?, swap=?, "
            "close_reason=?, closed_at=?, status='closed' WHERE ticket=?",
            (exit_price, profit, commission, swap, close_reason, closed_at or _utcnow(), ticket),
        )

    def update_trade_levels(self, ticket: int, stop_loss: float, take_profit: float) -> None:
        self.execute(
            "UPDATE trades SET stop_loss=?, take_profit=? WHERE ticket=?",
            (stop_loss, take_profit, ticket),
        )

    def update_trade_volume(self, ticket: int, volume: float) -> None:
        self.execute("UPDATE trades SET volume=? WHERE ticket=?", (volume, ticket))

    def get_trade(self, ticket: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM trades WHERE ticket=?", (ticket,))

    def open_trades(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM trades WHERE status='open' ORDER BY opened_at DESC")

    def recent_trades(self, limit: int = 100, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            return self.query(
                "SELECT * FROM trades WHERE symbol=? ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT ?",
                (symbol, limit),
            )
        return self.query(
            "SELECT * FROM trades ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT ?", (limit,)
        )

    def closed_trades_since(self, iso_timestamp: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM trades WHERE status='closed' AND closed_at >= ? ORDER BY closed_at ASC",
            (iso_timestamp,),
        )

    def trade_statistics(self) -> dict[str, Any]:
        row = self.query_one(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) AS losses, "
            "COALESCE(SUM(profit + commission + swap), 0) AS net_profit "
            "FROM trades WHERE status='closed'"
        ) or {}
        total = int(row.get("total") or 0)
        wins = int(row.get("wins") or 0)
        losses = int(row.get("losses") or 0)
        return {
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": round(wins / total * 100, 2) if total else 0.0,
            "net_profit": round(float(row.get("net_profit") or 0.0), 2),
        }

    # ------------------------------------------------------------------
    # positions snapshot
    # ------------------------------------------------------------------
    def upsert_position(self, position: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO positions(ticket, symbol, direction, volume, entry_price, current_price,"
            " stop_loss, take_profit, initial_sl, profit, swap, opened_at, magic, strategy,"
            " break_even_done, partial_done, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(ticket) DO UPDATE SET"
            "  current_price=excluded.current_price, stop_loss=excluded.stop_loss,"
            "  take_profit=excluded.take_profit, profit=excluded.profit, swap=excluded.swap,"
            "  volume=excluded.volume, updated_at=excluded.updated_at",
            (
                position["ticket"], position["symbol"], position["direction"], position["volume"],
                position.get("entry_price"), position.get("current_price"), position.get("stop_loss"),
                position.get("take_profit"), position.get("initial_sl"), position.get("profit"),
                position.get("swap"), position.get("opened_at"), position.get("magic"),
                position.get("strategy"), int(position.get("break_even_done", 0)),
                int(position.get("partial_done", 0)), _utcnow(),
            ),
        )

    def set_position_flag(self, ticket: int, flag: str, value: bool = True) -> None:
        if flag not in ("break_even_done", "partial_done"):
            raise ValueError(f"Unknown position flag '{flag}'")
        self.execute(f"UPDATE positions SET {flag}=? WHERE ticket=?", (int(value), ticket))

    def get_position(self, ticket: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM positions WHERE ticket=?", (ticket,))

    def delete_positions_except(self, tickets: Iterable[int]) -> None:
        tickets = list(tickets)
        if tickets:
            marks = ",".join("?" * len(tickets))
            self.execute(f"DELETE FROM positions WHERE ticket NOT IN ({marks})", tickets)
        else:
            self.execute("DELETE FROM positions")

    # ------------------------------------------------------------------
    # orders
    # ------------------------------------------------------------------
    def insert_order(self, order: dict[str, Any]) -> int:
        cur = self.execute(
            "INSERT INTO orders(ticket, symbol, order_type, action, volume, price, stop_loss,"
            " take_profit, retcode, retcode_text, comment, request_json, result_json, success,"
            " error_message, mode, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                order.get("ticket"), order["symbol"], order.get("order_type", ""), order["action"],
                order.get("volume"), order.get("price"), order.get("stop_loss"),
                order.get("take_profit"), order.get("retcode"), order.get("retcode_text"),
                order.get("comment"), json.dumps(order.get("request", {}), default=str),
                json.dumps(order.get("result", {}), default=str), int(bool(order.get("success"))),
                order.get("error_message"), order.get("mode", "demo"), _utcnow(),
            ),
        )
        return int(cur.lastrowid or 0)

    def recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------
    # signals
    # ------------------------------------------------------------------
    def insert_signal(self, signal: dict[str, Any]) -> int:
        cur = self.execute(
            "INSERT INTO signals(symbol, timeframe, strategy, direction, price, reason,"
            " indicators_json, executed, rejected_reason, bar_time, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                signal["symbol"], signal["timeframe"], signal["strategy"], signal["direction"],
                signal.get("price"), signal.get("reason"),
                json.dumps(signal.get("indicators", {}), default=str),
                int(bool(signal.get("executed"))), signal.get("rejected_reason"),
                signal.get("bar_time"), _utcnow(),
            ),
        )
        return int(cur.lastrowid or 0)

    def mark_signal(self, signal_id: int, executed: bool, rejected_reason: str | None = None) -> None:
        self.execute(
            "UPDATE signals SET executed=?, rejected_reason=? WHERE id=?",
            (int(executed), rejected_reason, signal_id),
        )

    def recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))

    def last_signal(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM signals WHERE direction != 'NONE' ORDER BY id DESC LIMIT 1")

    # ------------------------------------------------------------------
    # strategy runs
    # ------------------------------------------------------------------
    def start_strategy_run(self, run: dict[str, Any]) -> int:
        cur = self.execute(
            "INSERT INTO strategy_runs(strategy, symbols, timeframe, params_json, mode, started_at)"
            " VALUES(?,?,?,?,?,?)",
            (
                run["strategy"], json.dumps(run.get("symbols", [])), run["timeframe"],
                json.dumps(run.get("params", {}), default=str), run.get("mode", "demo"), _utcnow(),
            ),
        )
        return int(cur.lastrowid or 0)

    def stop_strategy_run(self, run_id: int, reason: str, signals: int, trades: int) -> None:
        self.execute(
            "UPDATE strategy_runs SET stopped_at=?, stop_reason=?, signals_count=?, trades_count=?"
            " WHERE id=?",
            (_utcnow(), reason, signals, trades, run_id),
        )

    def recent_strategy_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM strategy_runs ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------
    # logs
    # ------------------------------------------------------------------
    def insert_log(self, record: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO bot_logs(timestamp, category, message, symbol, source, details)"
            " VALUES(?,?,?,?,?,?)",
            (
                record.get("timestamp") or _utcnow(), record.get("category", "INFO"),
                record.get("message", ""), record.get("symbol"), record.get("source"),
                json.dumps(record.get("details", {}), default=str),
            ),
        )

    def recent_logs(self, limit: int = 200, category: str | None = None) -> list[dict[str, Any]]:
        if category and category.upper() != "ALL":
            return self.query(
                "SELECT * FROM bot_logs WHERE category=? ORDER BY id DESC LIMIT ?",
                (category.upper(), limit),
            )
        return self.query("SELECT * FROM bot_logs ORDER BY id DESC LIMIT ?", (limit,))

    def purge_logs(self, keep_last: int = 20_000) -> None:
        self.execute(
            "DELETE FROM bot_logs WHERE id NOT IN (SELECT id FROM bot_logs ORDER BY id DESC LIMIT ?)",
            (keep_last,),
        )

    # ------------------------------------------------------------------
    # daily performance
    # ------------------------------------------------------------------
    def ensure_day(self, day: str, starting_balance: float) -> dict[str, Any]:
        existing = self.query_one("SELECT * FROM daily_performance WHERE day=?", (day,))
        if existing:
            return existing
        self.execute(
            "INSERT INTO daily_performance(day, starting_balance, ending_balance, updated_at)"
            " VALUES(?,?,?,?)",
            (day, starting_balance, starting_balance, _utcnow()),
        )
        return self.query_one("SELECT * FROM daily_performance WHERE day=?", (day,)) or {}

    def update_day(self, day: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "starting_balance", "ending_balance", "realized_pnl", "trades", "wins",
            "losses", "max_drawdown", "daily_loss_hit",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k}=?" for k in updates)
        self.execute(
            f"UPDATE daily_performance SET {assignments}, updated_at=? WHERE day=?",
            [*updates.values(), _utcnow(), day],
        )

    def get_day(self, day: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM daily_performance WHERE day=?", (day,))

    def daily_history(self, limit: int = 60) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM daily_performance ORDER BY day DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------
    # backtests
    # ------------------------------------------------------------------
    def insert_backtest(self, result: dict[str, Any]) -> int:
        cur = self.execute(
            "INSERT INTO backtest_results(symbol, timeframe, strategy, start_date, end_date,"
            " initial_balance, final_balance, risk_percent, total_trades, winning_trades,"
            " losing_trades, win_rate, net_profit, max_drawdown, profit_factor, average_win,"
            " average_loss, largest_win, largest_loss, expectancy, params_json, equity_json,"
            " trades_json, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                result["symbol"], result["timeframe"], result["strategy"], result.get("start_date"),
                result.get("end_date"), result.get("initial_balance"), result.get("final_balance"),
                result.get("risk_percent"), result.get("total_trades"), result.get("winning_trades"),
                result.get("losing_trades"), result.get("win_rate"), result.get("net_profit"),
                result.get("max_drawdown"), result.get("profit_factor"), result.get("average_win"),
                result.get("average_loss"), result.get("largest_win"), result.get("largest_loss"),
                result.get("expectancy"), json.dumps(result.get("params", {}), default=str),
                json.dumps(result.get("equity_curve", []), default=str),
                json.dumps(result.get("trades", []), default=str), _utcnow(),
            ),
        )
        return int(cur.lastrowid or 0)

    def get_backtest(self, backtest_id: int) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM backtest_results WHERE id=?", (backtest_id,))
        if not row:
            return None
        for key, target in (("equity_json", "equity_curve"), ("trades_json", "trades"), ("params_json", "params")):
            try:
                row[target] = json.loads(row.pop(key) or ("[]" if target != "params" else "{}"))
            except json.JSONDecodeError:
                row[target] = [] if target != "params" else {}
        return row

    def list_backtests(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT id, symbol, timeframe, strategy, start_date, end_date, initial_balance,"
            " final_balance, total_trades, win_rate, net_profit, max_drawdown, profit_factor,"
            " created_at FROM backtest_results ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return rows

    def delete_backtest(self, backtest_id: int) -> None:
        self.execute("DELETE FROM backtest_results WHERE id=?", (backtest_id,))


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    """Process-wide database singleton."""
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
                log.info(f"Database ready at {_db.path}")
    return _db


def today_key() -> str:
    """Day bucket used by the daily-loss guard (server local date)."""
    return date.today().isoformat()
