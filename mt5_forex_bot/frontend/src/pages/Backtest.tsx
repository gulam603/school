/** Historical strategy testing over real MT5 candles. */

import { useState } from 'react';
import { EquityCurve } from '../components/EquityCurve';
import { useToast } from '../components/Toasts';
import { Banner, Card, Empty, Loading, Pill, Stat } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import { api, errorMessage } from '../lib/api';
import { daysAgo, isoDate, money, percent, profitFactor, shortDateTime, signedMoney } from '../lib/format';
import type { BacktestResult } from '../lib/types';

const SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD', 'XAUUSD'];
const TIMEFRAMES = ['M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

export function Backtest() {
  const toast = useToast();
  const saved = useAsync(() => api.backtests(), []);
  const [form, setForm] = useState({
    symbol: 'EURUSD',
    timeframe: 'H1',
    start_date: daysAgo(180),
    end_date: isoDate(new Date()),
    initial_balance: 10000,
    risk_percent: 1,
  });
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [running, setRunning] = useState(false);
  const [tab, setTab] = useState<'overview' | 'trades' | 'equity'>('overview');

  const run = async () => {
    setRunning(true);
    try {
      const response = await api.runBacktest(form);
      setResult(response);
      setTab('overview');
      toast.ok('Backtest complete', `${response.total_trades} trades simulated`);
      void saved.reload();
    } catch (cause) {
      toast.bad('Backtest failed', errorMessage(cause));
    } finally {
      setRunning(false);
    }
  };

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Backtest</h1>
        <span className="page-sub">Simulated over historical candles from your MT5 terminal</span>
      </div>

      <div className="grid-2">
        <Card title="Parameters">
          <div className="grid-2">
            <div className="field">
              <label className="field-label">Symbol</label>
              <select value={form.symbol} onChange={(event) => setForm({ ...form, symbol: event.target.value })}>
                {SYMBOLS.map((symbol) => <option key={symbol}>{symbol}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Timeframe</label>
              <select value={form.timeframe} onChange={(event) => setForm({ ...form, timeframe: event.target.value })}>
                {TIMEFRAMES.map((tf) => <option key={tf}>{tf}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Start date</label>
              <input type="date" value={form.start_date} onChange={(event) => setForm({ ...form, start_date: event.target.value })} />
            </div>
            <div className="field">
              <label className="field-label">End date</label>
              <input type="date" value={form.end_date} onChange={(event) => setForm({ ...form, end_date: event.target.value })} />
            </div>
            <div className="field">
              <label className="field-label">Initial balance</label>
              <input type="number" min={100} step={100} value={form.initial_balance}
                onChange={(event) => setForm({ ...form, initial_balance: Number(event.target.value) })} />
            </div>
            <div className="field">
              <label className="field-label">Risk per trade (%)</label>
              <input type="number" min={0.1} max={10} step={0.1} value={form.risk_percent}
                onChange={(event) => setForm({ ...form, risk_percent: Number(event.target.value) })} />
            </div>
          </div>
          <button className="btn primary" disabled={running} onClick={run}>
            {running ? <><span className="spinner" /> Running</> : 'Run backtest'}
          </button>
          <span className="faint small">
            Uses the strategy, ATR stop and risk rules currently configured on the Settings page.
          </span>
        </Card>

        <Card title="Saved results" bodyClass="flush">
          {!saved.data?.results.length ? (
            <Empty icon="--">No saved backtests yet</Empty>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 320, overflowY: 'auto' }}>
              <table className="data">
                <thead>
                  <tr><th>Run</th><th>Symbol</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Net</th><th /></tr>
                </thead>
                <tbody>
                  {saved.data.results.map((row) => (
                    <tr key={row.id}>
                      <td className="faint">{shortDateTime(String((row as unknown as { created_at: string }).created_at))}</td>
                      <td><strong>{row.symbol}</strong> <span className="faint">{row.timeframe}</span></td>
                      <td className="num">{row.total_trades}</td>
                      <td className="num">{row.win_rate?.toFixed(1)}%</td>
                      <td className={`num ${row.net_profit >= 0 ? 'positive' : 'negative'}`}>{signedMoney(row.net_profit)}</td>
                      <td>
                        <button
                          className="btn sm ghost"
                          onClick={async () => {
                            try {
                              setResult(await api.backtest(row.id!));
                              setTab('overview');
                            } catch (cause) {
                              toast.bad('Could not load result', errorMessage(cause));
                            }
                          }}
                        >
                          Open
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {running && <Loading label="Simulating" />}

      {result && (
        <>
          <div className="stat-grid">
            <Stat label="Total trades" value={result.total_trades} foot={`${result.average_bars_held ?? 0} bars avg hold`} />
            <Stat label="Win rate" value={`${result.win_rate.toFixed(1)}%`}
              foot={`${result.winning_trades}W / ${result.losing_trades}L`} />
            <Stat label="Net profit" value={signedMoney(result.net_profit)}
              tone={result.net_profit >= 0 ? 'positive' : 'negative'}
              foot={percent(result.net_profit_percent, 2, true)} />
            <Stat label="Max drawdown" value={percent(result.max_drawdown)} tone="negative"
              foot={money(result.max_drawdown_money)} />
            <Stat label="Profit factor" value={profitFactor(result.profit_factor)}
              foot={`${money(result.gross_profit)} / ${money(result.gross_loss)}`} />
            <Stat label="Expectancy" value={signedMoney(result.expectancy)}
              tone={result.expectancy >= 0 ? 'positive' : 'negative'} foot="per trade" />
          </div>

          <section className="card">
            <div className="tabs">
              {(['overview', 'equity', 'trades'] as const).map((name) => (
                <button key={name} className={`tab ${tab === name ? 'active' : ''}`} onClick={() => setTab(name)}>
                  {name[0].toUpperCase() + name.slice(1)}
                </button>
              ))}
            </div>
            <div className="card-body">
              {tab === 'overview' && (
                <div className="grid-2">
                  <div className="col">
                    <div className="kv"><span className="kv-key">Symbol / timeframe</span><span className="kv-val">{result.symbol} {result.timeframe}</span></div>
                    <div className="kv"><span className="kv-key">Period</span><span className="kv-val">{result.start_date} - {result.end_date}</span></div>
                    <div className="kv"><span className="kv-key">Strategy</span><span className="kv-val">{result.strategy_display_name ?? result.strategy}</span></div>
                    <div className="kv"><span className="kv-key">Candles</span><span className="kv-val">{result.candles ?? '--'}</span></div>
                    <div className="kv"><span className="kv-key">Risk per trade</span><span className="kv-val">{percent(result.risk_percent)}</span></div>
                    <div className="kv"><span className="kv-key">Initial balance</span><span className="kv-val">{money(result.initial_balance)}</span></div>
                    <div className="kv"><span className="kv-key">Final balance</span><span className="kv-val">{money(result.final_balance)}</span></div>
                  </div>
                  <div className="col">
                    <div className="kv"><span className="kv-key">Average win</span><span className="kv-val positive">{money(result.average_win)}</span></div>
                    <div className="kv"><span className="kv-key">Average loss</span><span className="kv-val negative">{money(result.average_loss)}</span></div>
                    <div className="kv"><span className="kv-key">Largest win</span><span className="kv-val positive">{money(result.largest_win)}</span></div>
                    <div className="kv"><span className="kv-key">Largest loss</span><span className="kv-val negative">{money(result.largest_loss)}</span></div>
                    <div className="kv"><span className="kv-key">Gross profit</span><span className="kv-val">{money(result.gross_profit)}</span></div>
                    <div className="kv"><span className="kv-key">Gross loss</span><span className="kv-val">{money(result.gross_loss)}</span></div>
                  </div>
                </div>
              )}

              {tab === 'equity' && (
                <EquityCurve
                  points={result.equity_curve.map((point) => ({ time: point.time, equity: point.equity }))}
                  initialBalance={result.initial_balance}
                />
              )}

              {tab === 'trades' && (
                !result.trades.length ? <Empty icon="--">No trades were taken in this period</Empty> : (
                  <div className="table-wrap" style={{ maxHeight: 420, overflowY: 'auto' }}>
                    <table className="data">
                      <thead>
                        <tr>
                          <th>#</th><th>Direction</th><th>Entry</th><th className="num">Price</th>
                          <th>Exit</th><th className="num">Price</th><th className="num">Lots</th>
                          <th className="num">P/L</th><th className="num">R</th><th>Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.trades.map((trade) => (
                          <tr key={trade.ticket}>
                            <td className="num faint">{trade.ticket}</td>
                            <td><Pill kind={trade.direction === 'BUY' ? 'buy' : 'sell'}>{trade.direction}</Pill></td>
                            <td className="faint">{trade.entry_time.slice(0, 16)}</td>
                            <td className="num">{trade.entry_price}</td>
                            <td className="faint">{trade.exit_time.slice(0, 16)}</td>
                            <td className="num">{trade.exit_price}</td>
                            <td className="num">{trade.volume}</td>
                            <td className={`num ${trade.profit >= 0 ? 'positive' : 'negative'}`}>{signedMoney(trade.profit)}</td>
                            <td className="num">{trade.r_multiple.toFixed(2)}R</td>
                            <td className="faint">{trade.exit_reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              )}
            </div>
          </section>

          <Banner kind="warn" title="Past performance does not guarantee future results">
            {result.disclaimer}
          </Banner>
        </>
      )}
    </main>
  );
}
