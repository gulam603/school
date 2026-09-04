/** The main trading screen: summary cards, chart, side panels, bottom tabs. */

import { useEffect, useMemo, useState } from 'react';
import { BottomTabs } from '../components/BottomTabs';
import { PriceChart } from '../components/PriceChart';
import { BotStatusPanel, RiskPanel, StrategyPanel, TradingControls } from '../components/panels';
import { Banner, Pill, Stat } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import type { LiveState } from '../hooks/useSnapshot';
import { api } from '../lib/api';
import { money, percent, pnlClass, signedMoney } from '../lib/format';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

export function Dashboard({ live }: { live: LiveState }) {
  const { snapshot, logs } = live;
  const symbols = snapshot?.bot.symbols ?? ['EURUSD'];
  const [symbol, setSymbol] = useState(symbols[0] ?? 'EURUSD');
  const [timeframe, setTimeframe] = useState(snapshot?.bot.timeframe ?? 'H1');

  useEffect(() => {
    if (snapshot?.bot.timeframe) setTimeframe(snapshot.bot.timeframe);
  }, [snapshot?.bot.timeframe]);

  const chart = useAsync(
    () => api.candles(symbol, timeframe, 400),
    [symbol, timeframe],
    { intervalMs: 15000 },
  );

  const account = snapshot?.account;
  const currency = account?.currency ?? 'USD';
  const stats = snapshot?.statistics;
  const risk = snapshot?.risk as any;
  const daily = risk?.daily;
  const totalPnl = useMemo(
    () => (account ? account.equity - account.balance : 0),
    [account],
  );

  return (
    <main className="page">
      {!snapshot?.connection.connected && (
        <Banner kind="warn" title="MetaTrader 5 is not connected">
          {snapshot?.connection.mt5_package_available
            ? 'Start the MT5 terminal, log into your demo account, then press Connect on the Settings page.'
            : 'The MetaTrader5 python package is unavailable on this machine - it requires Windows with MT5 installed.'}
          {snapshot?.connection.last_error ? ` (${snapshot.connection.last_error})` : ''}
        </Banner>
      )}
      {snapshot && !snapshot.mode.demo && (
        <Banner kind="bad" title="LIVE TRADING IS ACTIVE">
          Orders placed by this bot use real money. Set TRADING_MODE=demo in .env to go back to safety.
        </Banner>
      )}
      {daily?.limit_hit && (
        <Banner kind="bad" title="Daily loss limit reached">
          The bot has stopped opening new trades for today ({percent(daily.loss_percent)} of the
          {' '}{percent(daily.limit_percent)} limit). Existing positions are still managed.
        </Banner>
      )}

      <div className="stat-grid">
        <Stat label="Balance" value={money(account?.balance, currency)} foot={account ? `1:${account.leverage} leverage` : 'not connected'} />
        <Stat label="Equity" value={money(account?.equity, currency)} foot={`Free margin ${money(account?.free_margin, currency)}`} />
        <Stat
          label="Total P/L"
          value={signedMoney(totalPnl, currency)}
          tone={pnlClass(totalPnl)}
          foot={account?.balance ? percent((totalPnl / account.balance) * 100, 2, true) : '--'}
        />
        <Stat
          label="Today's P/L"
          value={signedMoney(snapshot?.today_pnl ?? 0, currency)}
          tone={pnlClass(snapshot?.today_pnl)}
          foot={daily ? `${daily.trades_today} trades - ${daily.wins_today}W / ${daily.losses_today}L` : '--'}
        />
        <Stat
          label="Open Positions"
          value={snapshot?.open_positions ?? 0}
          foot={risk?.max_open_trades ? `max ${risk.max_open_trades}` : '--'}
        />
        <Stat
          label="Win Rate"
          value={`${stats?.win_rate?.toFixed(1) ?? '0.0'}%`}
          foot={`${stats?.winning_trades ?? 0}W / ${stats?.losing_trades ?? 0}L of ${stats?.total_trades ?? 0}`}
        />
      </div>

      <div className="dash-grid">
        <div className="dash-left">
          <section className="card">
            <div className="chart-toolbar">
              <select value={symbol} onChange={(event) => setSymbol(event.target.value)} style={{ width: 130 }}>
                {symbols.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
              <div className="tf-group">
                {TIMEFRAMES.map((item) => (
                  <button
                    key={item}
                    className={`tf-btn ${timeframe === item ? 'active' : ''}`}
                    onClick={() => setTimeframe(item)}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <span className="spacer" />
              {chart.data && <Pill kind="muted">{chart.data.broker_symbol}</Pill>}
              {chart.loading && <span className="spinner" />}
              <button className="btn sm ghost" onClick={() => void chart.reload()}>Refresh</button>
            </div>
            {chart.error ? (
              <div className="empty">{chart.error}</div>
            ) : (
              <PriceChart
                data={chart.data}
                positions={snapshot?.positions ?? []}
                trades={snapshot?.recent_trades ?? []}
              />
            )}
          </section>

          <BottomTabs
            trades={snapshot?.recent_trades ?? []}
            positions={snapshot?.positions ?? []}
            logs={logs}
          />
        </div>

        <div className="dash-right">
          <BotStatusPanel snapshot={snapshot} />
          <StrategyPanel snapshot={snapshot} />
          <RiskPanel snapshot={snapshot} />
          <TradingControls snapshot={snapshot} onChanged={live.refresh} />
        </div>
      </div>
    </main>
  );
}
