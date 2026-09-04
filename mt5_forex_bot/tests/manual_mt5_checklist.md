# Live terminal checklist (MT5 demo account)

The automated suite (`pytest`) covers every calculation: indicators, lot sizing,
stop/target placement, the daily-loss guard, the backtester and the API. What it
deliberately does **not** do is fake order execution - a mocked trade server
proves nothing about how your broker behaves.

Run this checklist once against a **DEMO** account before leaving the bot alone.
Each item lists what to do and what you should see.

Prerequisites:

- MetaTrader 5 installed, running and logged into a **demo** account
- `Tools -> Options -> Expert Advisors -> Allow algorithmic trading` enabled
- the `Algo Trading` toolbar button switched **on**
- `.env` created from `.env.example` (leave `TRADING_MODE=demo`)
- `start.bat` executed; dashboard open

---

## 1. Connection

| # | Action | Expected |
|---|--------|----------|
| 1.1 | Open Settings -> MetaTrader 5 connection, press **Connect** | Green `CONNECTED` pill, your account number, broker and server appear |
| 1.2 | Look at the top bar | `DEMO MODE` pill is green; account type reads `DEMO` |
| 1.3 | Check the Dashboard cards | Balance, equity and free margin match the MT5 terminal exactly |
| 1.4 | Close MetaTrader 5 while the bot runs | Within one poll interval: `Disconnected`, a WARNING log line, and reconnect attempts with growing delays |
| 1.5 | Start MT5 again | The bot reconnects on its own and logs `Connected to MT5 - account ...` |

## 2. Market data

| # | Action | Expected |
|---|--------|----------|
| 2.1 | Open Market Watch | Every configured symbol shows a bid/ask that ticks; spread in points is plausible |
| 2.2 | Compare one symbol with MT5's own Market Watch | Prices agree to the last digit |
| 2.3 | Switch the chart timeframe through M1..D1 | Candles reload for each timeframe; EMA 20/50 lines follow the price |
| 2.4 | Compare the dashboard EMAs with `iMA` (EMA, close) in MT5 | The lines overlap (both are SMA-seeded EMAs) |
| 2.5 | Use a broker with suffixed symbols (e.g. `EURUSDm`) | Settings -> Broker symbol mapping resolves each symbol automatically, or lets you map it manually |

## 3. Signals

| # | Action | Expected |
|---|--------|----------|
| 3.1 | Set the timeframe to M1 and start the bot | Within a few minutes the Strategy page shows a live signal per symbol |
| 3.2 | Watch the Logs page | `SIGNAL` lines appear only when a candle **closes**, never mid-bar |
| 3.3 | Verify a signal by hand on the MT5 chart | The crossover is on the previous closed candle, and the RSI value matches |

## 4. Risk and order execution

| # | Action | Expected |
|---|--------|----------|
| 4.1 | Let the bot take a trade (or set M1 to speed it up) | Logs show `Risk calculated: 1.00% = ...`, `Lot size: ...`, then `DEMO ORDER EXECUTED` |
| 4.2 | Compare the lot in MT5 | Identical to the logged lot size |
| 4.3 | Check the risk by hand | `(entry - SL) x contract value x lots` is ~1% of the balance |
| 4.4 | Check the SL/TP in MT5 | Both are set on the position; TP distance is at least 2x the SL distance |
| 4.5 | Set `Max open trades` to 1, wait for a second signal | The signal is logged and rejected with "Maximum simultaneous trades reached" |
| 4.6 | Set `Maximum spread` to 1 point | Signals are rejected with "Spread too wide" |
| 4.7 | Set risk to 0.01% on a small account | The trade is skipped: "the broker's minimum volume would risk ..." - it is never rounded up |

## 5. Position management

| # | Action | Expected |
|---|--------|----------|
| 5.1 | Wait for an open trade to reach the break-even trigger | Log line `Break-even armed on #...`; the SL in MT5 moves to entry (+offset) |
| 5.2 | Wait for the trailing start level | `Trailing stop on #...` lines; the SL follows price and **never** moves backwards |
| 5.3 | Enable partial close and wait for the trigger | Part of the volume closes; the rest keeps its SL/TP |
| 5.4 | Set `Maximum holding time` to 1 hour | An older position is closed with reason `max_holding_time` |
| 5.5 | Edit SL/TP from the Positions page | The change appears in MT5 immediately |
| 5.6 | Press **Close all positions** | Every position closes; the trade log records each one |
| 5.7 | Press **EMERGENCY STOP** | The bot stops *and* all positions close |

## 6. Daily loss protection

| # | Action | Expected |
|---|--------|----------|
| 6.1 | Temporarily set `Maximum daily loss` below today's current loss | Red banner "Daily loss limit reached"; new signals are rejected |
| 6.2 | Check open positions | They are still monitored - break-even/trailing keep working |
| 6.3 | Restart the bot while the limit is hit | The limit is still in force: the day's starting balance is read back from the database, not reset |

## 7. Restart and resilience

| # | Action | Expected |
|---|--------|----------|
| 7.1 | Restart the backend with a position open | The position is picked up again; its SL/TP and break-even state survive |
| 7.2 | Kill the network for a minute | ERROR/WARNING logs, automatic reconnect, no crash |
| 7.3 | Trade a symbol whose market is closed (weekend) | "Market is closed" is logged, the bot continues with other symbols |
| 7.4 | Check `logs/mt5_bot.log` | It contains the same structured lines as the dashboard |

## 8. Backtesting

| # | Action | Expected |
|---|--------|----------|
| 8.1 | Open the symbol's chart in MT5 once so history downloads, then run a 6-month H1 backtest | Metrics and an equity curve appear within seconds |
| 8.2 | Read the trade list | No loss exceeds the configured risk percentage of the balance at that time |
| 8.3 | Confirm the disclaimer | "Past performance does not guarantee future results" is shown with every result |

## 9. Frontend / backend

| # | Action | Expected |
|---|--------|----------|
| 9.1 | Watch the top-right transport indicator | `WS` (WebSocket). Stop the backend: it falls back and the dashboard shows Disconnected instead of going blank |
| 9.2 | Restart the backend | The dashboard reconnects on its own |
| 9.3 | Open the browser devtools network tab | No password, token or credential ever appears in a response |
| 9.4 | Resize the window to phone width | The layout reflows; no horizontal scrolling |
