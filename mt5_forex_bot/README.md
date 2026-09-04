# MT5 Forex Bot

A personal **MetaTrader 5 demo trading terminal**: a Python trading engine plus a
React dashboard that monitors the market, generates signals, sizes positions from
risk, executes demo orders through MT5, manages open trades, backtests strategies
and logs everything.

Runs locally on Windows. **Demo mode is the default and cannot be left by accident.**

![demo mode](https://img.shields.io/badge/default-DEMO%20ONLY-22c55e) ![python](https://img.shields.io/badge/python-3.11%2B-7c5cff) ![tests](https://img.shields.io/badge/tests-181%20passing-22d3ee)

---

## Table of contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running it](#running-it)
- [The dashboard](#the-dashboard)
- [Strategy](#strategy)
- [Risk management](#risk-management)
- [Position management](#position-management)
- [Backtesting](#backtesting)
- [Broker symbol names](#broker-symbol-names)
- [Notifications](#notifications)
- [Demo vs live trading](#demo-vs-live-trading)
- [Security](#security)
- [Testing](#testing)
- [Project layout](#project-layout)
- [API reference](#api-reference)
- [Troubleshooting](#troubleshooting)
- [Adding a strategy](#adding-a-strategy)
- [Disclaimer](#disclaimer)

---

## What it does

- **Connects to your MetaTrader 5 terminal** - detects the installation, reads the
  account, balance, equity, free margin, open positions, live prices and history.
- **Generates signals** from a modular strategy engine (EMA crossover with an
  optional RSI filter ships as the default), evaluated on **closed candles only**.
- **Sizes every position from risk**: account balance -> risk percentage -> ATR
  stop distance -> the broker's contract specification. No martingale, no grid,
  no averaging down, ever.
- **Executes orders on your demo account** through MT5, with retries on requotes
  and price changes, and typed errors for everything else.
- **Manages open positions**: break-even, ATR trailing stop, optional partial
  close, maximum holding time, and a close-all emergency button.
- **Protects the account**: hard daily loss limit, maximum simultaneous trades,
  minimum risk/reward, maximum spread and free-margin checks.
- **Backtests** the same strategy code over real historical candles from your
  terminal, with equity curve and full statistics.
- **Shows everything** in a dark trading dashboard with live WebSocket updates.
- **Logs everything** in structured categories (INFO / SIGNAL / TRADE / RISK /
  WARNING / ERROR) to the console, a rotating file and SQLite.

## Requirements

| | |
|---|---|
| OS | Windows 10/11 (the `MetaTrader5` package is Windows only) |
| Python | 3.11 or newer |
| Node.js | 18 or newer (for the dashboard) |
| MetaTrader 5 | Installed and logged into a **demo** account |

The backtester, the indicators and the whole test suite also run on Linux/macOS -
only live trading needs Windows.

## Installation

```bat
git clone <your-repo-url>
cd mt5_forex_bot

copy .env.example .env
notepad .env           :: fill in your demo account details (optional)

start.bat              :: creates the venv, installs everything, starts both halves
```

`start.bat` checks Python, creates `.venv`, installs the Python and npm
dependencies on the first run, starts the backend and the dashboard, and opens the
browser.

Manual installation, if you prefer:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

cd frontend && npm install && cd ..

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev
```

## Configuration

All configuration lives in `.env` (copied from `.env.example`). Nothing sensitive
is ever stored in the frontend, the database or the API responses.

The most important keys:

| Key | Default | Meaning |
|---|---|---|
| `TRADING_MODE` | `demo` | `demo` or `live` - see [Demo vs live](#demo-vs-live-trading) |
| `ALLOW_LIVE_TRADING` | `false` | Second lock for live trading |
| `LIVE_TRADING_CONFIRMATION` | *(empty)* | Third lock: the exact confirmation phrase |
| `MT5_TERMINAL_PATH` | *(auto)* | Path to `terminal64.exe`; empty = auto-detect |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | *(empty)* | Optional: log in explicitly instead of attaching to the terminal's session |
| `TRADED_SYMBOLS` | 8 majors + XAUUSD | Symbols the bot watches |
| `DEFAULT_SYMBOL` / `DEFAULT_TIMEFRAME` | `EURUSD` / `H1` | Chart and trading defaults |
| `RISK_PER_TRADE_PERCENT` | `1.0` | Risk per trade, in percent of balance |
| `MAX_DAILY_LOSS_PERCENT` | `5.0` | Daily loss limit |
| `MAX_OPEN_TRADES` | `5` | Maximum simultaneous positions |
| `MIN_RISK_REWARD` | `2.0` | Minimum reward-to-risk ratio (1:2) |
| `ATR_SL_MULTIPLIER` | `1.5` | Stop distance = ATR x this |
| `TELEGRAM_*` | *(empty)* | Optional notifications |

Everything except the secrets can also be changed at runtime from the **Settings**,
**Strategy** and **Risk Management** pages; those changes are stored in SQLite and
survive a restart.

## Running it

| Script | What it does |
|---|---|
| `start.bat` | Checks Python and dependencies, starts the backend + dashboard, opens the browser |
| `stop.bat` | Stops the strategy engine, closes both console windows, frees the ports. **Open positions are not touched** |
| `restart.bat` | `stop.bat` followed by `start.bat` |

| URL | |
|---|---|
| http://localhost:5173 | Dashboard (development server) |
| http://127.0.0.1:8000 | Backend API (also serves the built dashboard) |
| http://127.0.0.1:8000/docs | Interactive API documentation |

Before starting the bot: MetaTrader 5 must be running, logged into your demo
account, with the **Algo Trading** button enabled.

## The dashboard

| Page | Contents |
|---|---|
| **Dashboard** | Balance / equity / total P/L / today's P/L / open positions / win rate, the candlestick chart with EMA overlays, trade markers and entry-SL-TP lines, the bot / strategy / risk panels, the trading controls, and the recent-trades / positions / pending-orders / logs tabs |
| **Market Watch** | Live bid/ask, spread, daily change and tradability for every configured symbol |
| **Positions** | Every open position, with per-position SL/TP editing and close buttons |
| **Orders** | Pending orders in the terminal plus the full request log the bot sent to MT5 |
| **History** | Closed trades, the signal log with rejection reasons, and daily performance |
| **Strategy** | Strategy selection, parameters, live per-symbol signals and past runs |
| **Backtest** | Run and review historical simulations with an equity curve |
| **Risk Management** | The daily loss budget and every risk / position-management setting |
| **Settings** | MT5 connection, symbol mapping, trading defaults, notifications, environment |
| **Logs** | The live structured log stream, filterable by category |

The dashboard updates over a WebSocket and falls back to HTTP polling if the
socket drops, so it degrades instead of freezing.

## Strategy

The default strategy is an **EMA crossover with an optional RSI filter**:

- **BUY** when EMA(20) crosses **above** EMA(50) on a closed candle,
  and (optionally) RSI < 70.
- **SELL** when EMA(20) crosses **below** EMA(50) on a closed candle,
  and (optionally) RSI > 30.

The still-forming candle is always removed before evaluation, so a cross that
appears mid-bar and disappears again can never trigger a trade.

Indicators available to strategies: **EMA, SMA, RSI, MACD, ATR, Bollinger Bands**.
EMA/RSI/ATR are seeded the way MetaTrader seeds them, so the values on the
dashboard match the ones in the terminal.

## Risk management

Every order passes all of these checks before it is sent:

1. **Daily loss limit** - realised + floating P/L against the day's starting
   balance. When the limit is reached: no new trades for the rest of the day, a
   clear warning in the UI, and existing positions stay managed.
2. **Maximum simultaneous trades**.
3. **One position per symbol** - the bot never stacks positions on one market.
4. **Spread check** against `max_spread_points`.
5. **ATR stop** - the stop distance is `ATR x multiplier`, widened if the broker's
   minimum stop level requires it.
6. **Minimum risk/reward** - the target is at least `min_risk_reward` times the
   stop distance (1:2 by default).
7. **Position size** - `balance x risk% / money-at-risk-per-lot`, using the
   broker's own tick value (and MT5's `order_calc_profit` when available, so
   cross-currency pairs are exact). The result is rounded **down** onto the lot
   step and clamped by both the broker's and your own maximum.
8. **Over-risk refusal** - if even the broker's minimum lot would risk more than
   the configured percentage, the trade is skipped rather than taken too large.
9. **Free margin** check via `order_calc_margin`.

What the bot will never do: **martingale, grid trading, averaging down, or
increasing the lot size after a loss.** Position size is a pure function of
balance, risk percentage and stop distance - the outcome of previous trades is
not an input, which is verified by the test suite.

## Position management

All of these are individually switchable on the Risk Management page:

| Feature | Default | Behaviour |
|---|---|---|
| Break-even | on, at 1.0R | Moves the stop to entry + offset once the trade is 1R in profit |
| Trailing stop | on, from 1.5R | Trails the stop at 1.0 x ATR behind price, never backwards |
| Partial close | off | Closes a configurable share of the position at a configurable R |
| Maximum holding time | 48h | Closes a position that has been open too long |
| Close all | button | Closes every position at market |
| Emergency stop | button | Stops the bot **and** closes every position |

Positions closed by the broker (stop loss or take profit hit) are reconciled
against the terminal's deal history, so the local trade log always matches MT5.

## Backtesting

The backtester replays real historical candles from your terminal through the same
strategy and the same risk functions the live bot uses.

Modelling choices, all deliberately conservative:

- a signal on the close of bar *i* is entered at the **open of bar i+1**,
- the entry pays the spread,
- if a bar's range covers both the stop and the target, the **stop** is assumed
  to have been hit first,
- break-even and trailing adjustments are evaluated on bar closes,
- position size is recalculated from the running balance for every trade.

Reported: total / winning / losing trades, win rate, net profit, maximum drawdown,
profit factor, average and largest win/loss, expectancy, and the equity curve.

> Backtest results are a simulation. **Historical performance does not guarantee
> future results.** Real trading additionally involves slippage, requotes,
> variable spread, swap and execution delays.

## Broker symbol names

Brokers rename instruments: `EURUSD`, `EURUSDm`, `EURUSD.a`, `EURUSD_i`,
`EURUSD-ECN`, `FX_EURUSD` are all the same market. The bot always speaks in
canonical names and resolves them in this order:

1. a manual override you set on the Settings page,
2. the configured `SYMBOL_PREFIX` / `SYMBOL_SUFFIX`,
3. auto-detection against the broker's own symbol list,
4. failure - the symbol is reported as unavailable rather than guessed wrong.

Settings -> **Broker symbol mapping** shows the resolution for every symbol and
lets you pin one by hand.

## Notifications

Optional Telegram notifications for: bot started/stopped, buy/sell signals, trade
opened/closed, SL hit, TP hit, daily loss limit reached, MT5 disconnected/
reconnected and critical errors.

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, then enable the switch
on the Settings page. Messages are delivered by a background worker, so a slow
Telegram API can never delay an order.

## Demo vs live trading

**The default installation cannot place a real-money order.** Three independent
switches must all be set in `.env`:

```ini
TRADING_MODE=live
ALLOW_LIVE_TRADING=true
LIVE_TRADING_CONFIRMATION=I UNDERSTAND THE RISK OF LIVE TRADING
```

There is deliberately **no button in the UI** for this. On top of the three
switches, the order manager reads the account's `trade_mode` from the terminal
itself: if it is a real-money account and the locks are not all open, every order
is refused with a `LiveTradingBlockedError`.

When live trading is unlocked the dashboard shows a red `LIVE TRADING` pill and a
persistent warning banner.

## Security

- Credentials (MT5 login/password, Telegram token) live **only** in `.env`, which
  is in `.gitignore`.
- The backend never returns a credential: `/api/settings` reports *whether*
  something is configured, never its value - and there is a test that fails if a
  password or token ever appears in an API response.
- The frontend holds no secrets and talks only to `127.0.0.1`.
- The server binds to `127.0.0.1` by default; do not expose it to a network.
- The SQLite database stores trades, signals, orders and logs - never credentials.

## Testing

```bat
pytest                    :: 181 tests
pytest -v tests/test_risk_manager.py
cd frontend && npm run typecheck
```

| Suite | Covers |
|---|---|
| `test_indicators.py` | RSI/EMA/ATR against published reference values, crossovers, bounds |
| `test_risk_manager.py` | Lot sizing, stop/target placement, R:R, lot bounds, over-risk refusal, no-martingale |
| `test_daily_loss_guard.py` | Realised + floating losses, the limit trip point, restart persistence, fail-closed behaviour |
| `test_strategy.py` | Signal generation, the RSI filter, warm-up, the strategy contract |
| `test_backtester.py` | Metrics, determinism, JSON safety, and that no simulated loss exceeds the risk setting |
| `test_position_manager.py` | R multiples, stops that only ever move towards profit, SL/TP classification |
| `test_database.py` | Every table, trade lifecycle, statistics, config round trip |
| `test_config.py` | Validation, clamping, transactional updates, the demo/live gate, secret leakage |
| `test_api.py` | Every route, 503 degradation without MT5, WebSocket handshake, credential safety |
| `test_symbols_and_errors.py` | Broker symbol mapping and the MT5 return-code table |
| `test_market_data.py` | Volume snapping, price rounding, pip size |

Order *execution* is verified against a real demo terminal rather than a mock -
see **`tests/manual_mt5_checklist.md`** for that checklist.

## Project layout

```
mt5_forex_bot/
├── backend/
│   ├── main.py              FastAPI app, WebSocket hub, static dashboard
│   ├── app_state.py         Shared state, snapshot builder, event fan-out
│   ├── config.py            Settings (.env, secrets) + BotConfig (runtime)
│   ├── mt5_connection.py    Terminal discovery, connect, auto-reconnect
│   ├── mt5_errors.py        Typed error hierarchy
│   ├── symbols.py           Broker symbol mapping
│   ├── market_data.py       Quotes, candles, contract specifications
│   ├── indicators.py        EMA, SMA, RSI, MACD, ATR, Bollinger Bands
│   ├── strategy_engine.py   The bot loop
│   ├── risk_manager.py      Lot sizing, stops, daily loss guard
│   ├── order_manager.py     Order execution + the demo/live gate
│   ├── position_manager.py  Break-even, trailing, partial close, time stop
│   ├── backtester.py        Historical simulation
│   ├── database.py          SQLite persistence
│   ├── notifications.py     Telegram
│   ├── logger.py            Structured logging
│   ├── schemas.py           API request models
│   └── routers/             system, market, trading, analysis
├── strategies/
│   ├── base_strategy.py     Strategy contract + registry
│   └── ema_crossover.py     The default strategy
├── frontend/                React + TypeScript dashboard (Vite)
│   └── src/
│       ├── components/      Chart, panels, tables, UI primitives
│       ├── pages/           The ten dashboard pages
│       ├── hooks/           WebSocket live state, async loader
│       └── lib/             API client, formatters, types
├── data/                    SQLite database (gitignored)
├── logs/                    Rotating log files (gitignored)
├── tests/                   181 automated tests + the live checklist
├── .env.example
├── requirements.txt
├── start.bat / stop.bat / restart.bat
└── README.md
```

## API reference

Full interactive documentation at `/docs` while the backend runs.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness, demo mode, connection state |
| GET | `/api/status` | Full dashboard snapshot |
| GET | `/api/mode` | Demo/live banner data |
| GET/POST | `/api/connection`, `/api/connection/connect` | Connection status / connect |
| GET | `/api/connection/terminals` | Detected MT5 installations |
| GET | `/api/account`, `/api/terminal` | Account and terminal information |
| GET/PUT | `/api/settings` | Runtime configuration |
| GET/POST/DELETE | `/api/settings/symbols[/override]` | Broker symbol mapping |
| GET | `/api/market/watch`, `/tick/{s}`, `/candles/{s}`, `/indicators/{s}`, `/spec/{s}` | Market data |
| POST | `/api/bot/start`, `/api/bot/stop`, `/api/bot/emergency-stop` | Bot control |
| GET | `/api/positions` | Open positions |
| PUT/POST | `/api/positions/{ticket}`, `/api/positions/{ticket}/close`, `/api/positions/close-all` | Position actions |
| GET | `/api/orders/pending`, `/api/orders/log` | Orders |
| GET | `/api/history/trades`, `/api/history/deals`, `/api/performance/daily`, `/api/signals` | History |
| GET | `/api/strategies`, `/api/strategy/runs` | Strategies |
| POST/GET/DELETE | `/api/backtest/run`, `/api/backtest/results[/{id}]` | Backtesting |
| GET | `/api/risk`, `/api/logs` | Risk summary, logs |
| WS | `/ws` | Live snapshots, log lines, signal and trade events |

## Troubleshooting

**"The MetaTrader5 python package is not available"**
You are not on Windows, or the package is missing: `pip install MetaTrader5`.

**"Could not connect to MetaTrader 5"**
Start the MT5 terminal and log in first. If you have several installations, pick
the right `terminal64.exe` on the Settings page or set `MT5_TERMINAL_PATH`.

**"Algo Trading is switched off in the MetaTrader 5 terminal"**
Press the `Algo Trading` button in MT5, and enable
`Tools -> Options -> Expert Advisors -> Allow algorithmic trading`.

**"Symbol 'X' is not available at this broker"**
Your broker renames it. Settings -> Broker symbol mapping -> map it manually, or
set `SYMBOL_SUFFIX` / `SYMBOL_PREFIX`.

**"No historical candles for ... "**
Open that symbol's chart in MT5 once on the timeframe you want, so the terminal
downloads the history, then run the backtest again.

**"Invalid stops (SL/TP too close to price)"**
The broker's minimum stop distance is larger than your ATR stop. Increase
`ATR_SL_MULTIPLIER` or trade a higher timeframe.

**The bot runs but never trades**
Normal: it waits for a crossover on a **closed** candle. Check the Logs page -
every rejected signal is recorded with its reason.

**Dashboard shows "Backend unreachable"**
The backend is not running, or is on another port. Start it, or check
`API_PORT` in `.env`.

## Adding a strategy

1. Create `strategies/my_strategy.py`.
2. Subclass `BaseStrategy`, implement `prepare()` and `generate_signal()`.
3. Decorate the class with `@register_strategy`.

```python
from strategies.base_strategy import BUY, BaseStrategy, ParamSpec, Signal, register_strategy


@register_strategy
class MyStrategy(BaseStrategy):
    name = "my_strategy"
    display_name = "My Strategy"
    description = "What it trades and why."

    @classmethod
    def param_specs(cls):
        return [ParamSpec("lookback", "Lookback", "int", 20, 2, 500, 1)]

    def prepare(self, frame):
        data = frame.copy()
        data["signal_line"] = data["close"].rolling(int(self.params["lookback"])).mean()
        return data

    def generate_signal(self, frame, symbol, timeframe):
        last = frame.iloc[-1]
        if last["close"] > last["signal_line"]:
            return Signal(symbol=symbol, timeframe=timeframe, direction=BUY,
                          price=float(last["close"]), reason="close above the average",
                          strategy=self.name, atr=float(last.get("atr", 0)))
        return self.no_signal(symbol, timeframe, "no setup", frame)
```

It appears automatically in the Strategy dropdown, the API and the backtester.
Strategies never place orders: they return a signal, and the risk manager decides
whether and how big to trade.

## Disclaimer

This software is a personal trading tool provided as-is, for use on a
**MetaTrader 5 demo account**. Trading foreign exchange and CFDs carries a high
level of risk and can result in the loss of all of your capital. Automated systems
can fail in ways manual trading does not - connectivity loss, broker rejections,
unexpected market conditions and bugs. Backtested performance never guarantees
future results. You alone are responsible for any account this software is
connected to and for any order it places. Test on a demo account for a long time
before even considering anything else.
