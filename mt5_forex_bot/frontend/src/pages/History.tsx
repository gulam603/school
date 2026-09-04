/** Closed trades, signals and the daily performance roll-up. */

import { useState } from 'react';
import { TradesTable } from '../components/tables';
import { Empty, Loading, Pill, Stat } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import { api } from '../lib/api';
import { money, percent, shortDateTime, signedMoney } from '../lib/format';

const TABS = ['Trades', 'Signals', 'Daily performance'] as const;

export function History() {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Trades');
  const history = useAsync(() => api.trades(300), [], { intervalMs: 10000 });
  const signals = useAsync(() => api.signals(100), [], { immediate: false });
  const daily = useAsync(() => api.dailyPerformance(60), [], { immediate: false });

  const stats = history.data?.statistics;
  const closed = (history.data?.trades ?? []).filter((trade) => trade.status === 'closed');
  const grossProfit = closed.filter((t) => t.profit > 0).reduce((sum, t) => sum + t.profit, 0);
  const grossLoss = Math.abs(closed.filter((t) => t.profit < 0).reduce((sum, t) => sum + t.profit, 0));

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">History</h1>
        <span className="page-sub">Everything this bot has traded, signalled and logged per day</span>
      </div>

      <div className="grid-4">
        <Stat label="Closed trades" value={stats?.total_trades ?? 0} />
        <Stat label="Win rate" value={`${(stats?.win_rate ?? 0).toFixed(1)}%`}
          foot={`${stats?.winning_trades ?? 0}W / ${stats?.losing_trades ?? 0}L`} />
        <Stat label="Net profit" value={signedMoney(stats?.net_profit ?? 0)}
          tone={(stats?.net_profit ?? 0) >= 0 ? 'positive' : 'negative'} />
        <Stat label="Profit factor" value={grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : 'INF'}
          foot={`${money(grossProfit)} / ${money(grossLoss)}`} />
      </div>

      <section className="card">
        <div className="tabs">
          {TABS.map((name) => (
            <button
              key={name}
              className={`tab ${tab === name ? 'active' : ''}`}
              onClick={() => {
                setTab(name);
                if (name === 'Signals') void signals.reload();
                if (name === 'Daily performance') void daily.reload();
              }}
            >
              {name}
            </button>
          ))}
        </div>
        <div className="card-body flush">
          {tab === 'Trades' && (
            history.loading && !history.data ? <Loading />
              : history.error ? <Empty icon="!">{history.error}</Empty>
              : <TradesTable trades={history.data?.trades ?? []} />
          )}

          {tab === 'Signals' && (
            !signals.data ? <Loading />
              : !signals.data.signals.length ? <Empty icon="--">No signals recorded yet</Empty>
              : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Time</th><th>Symbol</th><th>TF</th><th>Direction</th>
                        <th className="num">Price</th><th>Executed</th><th className="wrap">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {signals.data.signals.map((signal) => (
                        <tr key={String(signal.id)}>
                          <td className="faint">{shortDateTime(String(signal.created_at))}</td>
                          <td><strong>{String(signal.symbol)}</strong></td>
                          <td className="faint">{String(signal.timeframe)}</td>
                          <td>
                            <Pill kind={signal.direction === 'BUY' ? 'buy' : signal.direction === 'SELL' ? 'sell' : 'muted'}>
                              {String(signal.direction)}
                            </Pill>
                          </td>
                          <td className="num">{signal.price ? Number(signal.price).toFixed(5) : '--'}</td>
                          <td>
                            <Pill kind={signal.executed ? 'ok' : 'muted'}>{signal.executed ? 'TRADED' : 'SKIPPED'}</Pill>
                          </td>
                          <td className="wrap faint">
                            {String(signal.rejected_reason || signal.reason || '')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
          )}

          {tab === 'Daily performance' && (
            !daily.data ? <Loading />
              : !daily.data.days.length ? <Empty icon="--">No trading days recorded yet</Empty>
              : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Day</th><th className="num">Start balance</th><th className="num">End balance</th>
                        <th className="num">Realized P/L</th><th className="num">Trades</th>
                        <th className="num">W/L</th><th>Loss limit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {daily.data.days.map((day) => {
                        const pnl = Number(day.realized_pnl ?? 0);
                        const start = Number(day.starting_balance ?? 0);
                        return (
                          <tr key={String(day.day)}>
                            <td><strong>{String(day.day)}</strong></td>
                            <td className="num">{money(start)}</td>
                            <td className="num">{money(Number(day.ending_balance ?? 0))}</td>
                            <td className={`num ${pnl >= 0 ? 'positive' : 'negative'}`}>
                              {signedMoney(pnl)} <span className="faint">({percent(start ? (pnl / start) * 100 : 0, 2, true)})</span>
                            </td>
                            <td className="num">{String(day.trades ?? 0)}</td>
                            <td className="num faint">{String(day.wins ?? 0)}/{String(day.losses ?? 0)}</td>
                            <td><Pill kind={day.daily_loss_hit ? 'bad' : 'ok'}>{day.daily_loss_hit ? 'HIT' : 'OK'}</Pill></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )
          )}
        </div>
      </section>
    </main>
  );
}
