/** Strategy selection, parameters and the live per-symbol signal board. */

import { useEffect, useState } from 'react';
import { useToast } from '../components/Toasts';
import { Banner, Card, Empty, Loading, NumberField, Pill, SelectField, Switch } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import type { LiveState } from '../hooks/useSnapshot';
import { api, errorMessage } from '../lib/api';
import { shortDateTime } from '../lib/format';
import type { BotConfig } from '../lib/types';

export function StrategyPage({ live }: { live: LiveState }) {
  const toast = useToast();
  const strategies = useAsync(() => api.strategies(), []);
  const settings = useAsync(() => api.settings(), []);
  const runs = useAsync(() => api.strategyRuns(10), []);
  const [draft, setDraft] = useState<BotConfig | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (settings.data?.config) setDraft(settings.data.config);
  }, [settings.data]);

  const bot = live.snapshot?.bot;
  const active = strategies.data?.strategies.find((item) => item.name === (draft?.strategy ?? bot?.strategy));

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const response = await api.updateSettings({
        strategy: draft.strategy,
        timeframe: draft.timeframe,
        ema_fast: draft.ema_fast,
        ema_slow: draft.ema_slow,
        use_rsi_filter: draft.use_rsi_filter,
        rsi_period: draft.rsi_period,
        rsi_buy_max: draft.rsi_buy_max,
        rsi_sell_min: draft.rsi_sell_min,
        atr_period: draft.atr_period,
      });
      setDraft(response.config);
      toast.ok('Strategy settings saved', bot?.running ? 'Restart the bot to apply on the next tick' : undefined);
      await live.refresh();
    } catch (cause) {
      toast.bad('Could not save', errorMessage(cause));
    } finally {
      setSaving(false);
    }
  };

  if (strategies.loading || !draft) return <main className="page"><Loading label="Loading strategies" /></main>;

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Strategy</h1>
        <span className="page-sub">Signals are evaluated on closed candles only</span>
        <span className="spacer" />
        {bot?.running && <Pill kind="ok">Bot running - changes apply after a restart</Pill>}
      </div>

      <div className="grid-2">
        <Card title="Active strategy">
          <SelectField
            label="Strategy"
            value={draft.strategy}
            options={(strategies.data?.strategies ?? []).map((item) => ({ value: item.name, label: item.display_name }))}
            onChange={(value) => setDraft({ ...draft, strategy: value })}
          />
          {active && <p className="muted small" style={{ lineHeight: 1.55, margin: 0 }}>{active.description}</p>}

          <SelectField
            label="Timeframe"
            value={draft.timeframe}
            options={(settings.data?.timeframes ?? ['H1']).map((item) => ({ value: item, label: item }))}
            onChange={(value) => setDraft({ ...draft, timeframe: value })}
          />

          <div className="grid-2">
            <NumberField label="EMA Fast" value={draft.ema_fast} min={2} max={1000}
              onChange={(value) => setDraft({ ...draft, ema_fast: value })} />
            <NumberField label="EMA Slow" value={draft.ema_slow} min={3} max={5000}
              onChange={(value) => setDraft({ ...draft, ema_slow: value })} />
          </div>

          <Switch
            checked={draft.use_rsi_filter}
            onChange={(value) => setDraft({ ...draft, use_rsi_filter: value })}
            label="Use RSI filter"
          />
          <div className="grid-3">
            <NumberField label="RSI Period" value={draft.rsi_period} min={2} max={100}
              onChange={(value) => setDraft({ ...draft, rsi_period: value })} />
            <NumberField label="Buy below RSI" value={draft.rsi_buy_max} min={1} max={100}
              onChange={(value) => setDraft({ ...draft, rsi_buy_max: value })} />
            <NumberField label="Sell above RSI" value={draft.rsi_sell_min} min={0} max={99}
              onChange={(value) => setDraft({ ...draft, rsi_sell_min: value })} />
          </div>
          <NumberField label="ATR period (stop distance)" value={draft.atr_period} min={2} max={200}
            onChange={(value) => setDraft({ ...draft, atr_period: value })} />

          <button className="btn primary" disabled={saving} onClick={save}>
            {saving ? <span className="spinner" /> : null} Save strategy settings
          </button>
        </Card>

        <div className="col">
          <Card title="Live signals" bodyClass="flush">
            {!bot || !Object.keys(bot.current_signals ?? {}).length ? (
              <Empty icon="--">Start the bot to see live signal evaluation</Empty>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr><th>Symbol</th><th>Signal</th><th className="num">Price</th><th className="wrap">Reason</th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(bot.current_signals).map(([symbol, signal]) => (
                      <tr key={symbol}>
                        <td><strong>{symbol}</strong></td>
                        <td>
                          <Pill kind={signal.direction === 'BUY' ? 'buy' : signal.direction === 'SELL' ? 'sell' : 'muted'}>
                            {signal.direction}
                          </Pill>
                        </td>
                        <td className="num">{signal.price?.toFixed(5) ?? '--'}</td>
                        <td className="wrap faint">{signal.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="Recent runs" bodyClass="flush">
            {!runs.data?.runs.length ? (
              <Empty icon="--">The bot has not been started yet</Empty>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr><th>Started</th><th>Strategy</th><th>TF</th><th className="num">Signals</th><th className="num">Trades</th><th>Stopped</th></tr>
                  </thead>
                  <tbody>
                    {runs.data.runs.map((run) => (
                      <tr key={String(run.id)}>
                        <td className="faint">{shortDateTime(String(run.started_at))}</td>
                        <td>{String(run.strategy)}</td>
                        <td className="faint">{String(run.timeframe)}</td>
                        <td className="num">{String(run.signals_count ?? 0)}</td>
                        <td className="num">{String(run.trades_count ?? 0)}</td>
                        <td className="faint">{run.stopped_at ? String(run.stop_reason ?? 'stopped') : 'running'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      </div>

      <Banner kind="info" title="How a signal becomes a trade">
        A crossover is only accepted on a <strong>closed</strong> candle. It then has to clear the
        risk manager (daily loss limit, maximum open trades, spread, minimum risk/reward, lot bounds
        and free margin) before an order is sent. Every rejection is recorded with its reason on the
        History page.
      </Banner>
    </main>
  );
}
