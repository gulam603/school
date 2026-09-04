/** Risk rules, the daily loss budget and the position-management switches. */

import { useEffect, useState } from 'react';
import { useToast } from '../components/Toasts';
import { Banner, Card, Loading, Meter, NumberField, Stat, Switch } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import type { LiveState } from '../hooks/useSnapshot';
import { api, errorMessage } from '../lib/api';
import { money, percent, signedMoney } from '../lib/format';
import type { BotConfig } from '../lib/types';

export function RiskManagement({ live }: { live: LiveState }) {
  const toast = useToast();
  const settings = useAsync(() => api.settings(), []);
  const [draft, setDraft] = useState<BotConfig | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (settings.data?.config) setDraft(settings.data.config);
  }, [settings.data]);

  const risk = live.snapshot?.risk as any;
  const daily = risk?.daily;

  const save = async (fields: Partial<BotConfig>) => {
    setSaving(true);
    try {
      const response = await api.updateSettings(fields as Record<string, unknown>);
      setDraft(response.config);
      toast.ok('Risk settings saved');
      await live.refresh();
    } catch (cause) {
      toast.bad('Could not save', errorMessage(cause));
    } finally {
      setSaving(false);
    }
  };

  if (!draft) return <main className="page"><Loading label="Loading risk settings" /></main>;

  const lossUsed = daily?.loss_percent ?? 0;
  const lossLimit = daily?.limit_percent ?? draft.max_daily_loss_percent;

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Risk Management</h1>
        <span className="page-sub">Hard limits enforced on every order, before it is sent</span>
      </div>

      {daily?.limit_hit && (
        <Banner kind="bad" title="Daily loss limit reached">
          New trades are blocked until tomorrow. Open positions continue to be managed
          (break-even, trailing stop and stop loss all stay active).
        </Banner>
      )}

      <div className="grid-4">
        <Stat label="Day start balance" value={money(daily?.starting_balance)} foot={daily?.day ?? '--'} />
        <Stat
          label="Today's P/L"
          value={signedMoney(daily?.total_pnl ?? 0)}
          tone={(daily?.total_pnl ?? 0) >= 0 ? 'positive' : 'negative'}
          foot={`realized ${money(daily?.realized_pnl ?? 0)} / floating ${money(daily?.floating_pnl ?? 0)}`}
        />
        <Stat
          label="Daily loss used"
          value={percent(lossUsed)}
          tone={lossUsed >= lossLimit ? 'negative' : ''}
          foot={`limit ${percent(lossLimit)}`}
        />
        <Stat
          label="Open trades"
          value={`${risk?.open_trades ?? 0} / ${risk?.max_open_trades ?? draft.max_open_trades}`}
          foot={`${daily?.trades_today ?? 0} trades today`}
        />
      </div>

      <Card title="Daily loss budget">
        <Meter value={lossUsed} max={lossLimit} tone={lossUsed >= lossLimit ? 'bad' : lossUsed >= lossLimit * 0.6 ? 'warn' : 'ok'} />
        <div className="row small faint">
          <span>{percent(lossUsed)} used</span>
          <span className="spacer" />
          <span>{percent(Math.max(0, lossLimit - lossUsed))} remaining</span>
        </div>
      </Card>

      <div className="grid-2">
        <Card title="Trade risk">
          <NumberField label="Risk per trade" suffix="%" value={draft.risk_per_trade_percent} min={0.01} max={10} step={0.05}
            help="Percentage of the account balance risked between entry and stop loss"
            onChange={(value) => setDraft({ ...draft, risk_per_trade_percent: value })} />
          <NumberField label="Maximum daily loss" suffix="%" value={draft.max_daily_loss_percent} min={0.1} max={50} step={0.5}
            help="When reached, the bot stops opening new trades for the rest of the day"
            onChange={(value) => setDraft({ ...draft, max_daily_loss_percent: value })} />
          <NumberField label="Maximum simultaneous trades" value={draft.max_open_trades} min={1} max={50}
            onChange={(value) => setDraft({ ...draft, max_open_trades: value })} />
          <NumberField label="Minimum risk/reward" value={draft.min_risk_reward} min={0.1} max={20} step={0.1}
            help="A 1:2 minimum means the take profit is at least twice the stop distance"
            onChange={(value) => setDraft({ ...draft, min_risk_reward: value })} />
          <div className="grid-2">
            <NumberField label="ATR period" value={draft.atr_period} min={2} max={200}
              onChange={(value) => setDraft({ ...draft, atr_period: value })} />
            <NumberField label="ATR stop multiplier" value={draft.atr_sl_multiplier} min={0.1} max={20} step={0.1}
              onChange={(value) => setDraft({ ...draft, atr_sl_multiplier: value })} />
          </div>
          <div className="grid-2">
            <NumberField label="Maximum lot size" value={draft.max_lot_size} min={0.01} max={500} step={0.01}
              onChange={(value) => setDraft({ ...draft, max_lot_size: value })} />
            <NumberField label="Maximum spread" suffix="points" value={draft.max_spread_points} min={0} max={10000}
              onChange={(value) => setDraft({ ...draft, max_spread_points: value })} />
          </div>
          <button className="btn primary" disabled={saving} onClick={() => save({
            risk_per_trade_percent: draft.risk_per_trade_percent,
            max_daily_loss_percent: draft.max_daily_loss_percent,
            max_open_trades: draft.max_open_trades,
            min_risk_reward: draft.min_risk_reward,
            atr_period: draft.atr_period,
            atr_sl_multiplier: draft.atr_sl_multiplier,
            max_lot_size: draft.max_lot_size,
            max_spread_points: draft.max_spread_points,
          })}>
            Save risk settings
          </button>
        </Card>

        <Card title="Position management">
          <Switch checked={draft.break_even_enabled} label="Break-even stop"
            onChange={(value) => setDraft({ ...draft, break_even_enabled: value })} />
          <div className="grid-2">
            <NumberField label="Trigger at" suffix="R" value={draft.break_even_trigger_r} min={0.1} max={20} step={0.1}
              disabled={!draft.break_even_enabled}
              onChange={(value) => setDraft({ ...draft, break_even_trigger_r: value })} />
            <NumberField label="Offset" suffix="points" value={draft.break_even_offset_points} min={0} max={10000}
              disabled={!draft.break_even_enabled}
              onChange={(value) => setDraft({ ...draft, break_even_offset_points: value })} />
          </div>

          <Switch checked={draft.trailing_stop_enabled} label="Trailing stop"
            onChange={(value) => setDraft({ ...draft, trailing_stop_enabled: value })} />
          <div className="grid-2">
            <NumberField label="Start at" suffix="R" value={draft.trailing_start_r} min={0.1} max={20} step={0.1}
              disabled={!draft.trailing_stop_enabled}
              onChange={(value) => setDraft({ ...draft, trailing_start_r: value })} />
            <NumberField label="Distance" suffix="x ATR" value={draft.trailing_distance_atr} min={0.1} max={20} step={0.1}
              disabled={!draft.trailing_stop_enabled}
              onChange={(value) => setDraft({ ...draft, trailing_distance_atr: value })} />
          </div>

          <Switch checked={draft.partial_close_enabled} label="Partial close"
            onChange={(value) => setDraft({ ...draft, partial_close_enabled: value })} />
          <div className="grid-2">
            <NumberField label="Close at" suffix="R" value={draft.partial_close_at_r} min={0.1} max={20} step={0.1}
              disabled={!draft.partial_close_enabled}
              onChange={(value) => setDraft({ ...draft, partial_close_at_r: value })} />
            <NumberField label="Close" suffix="% of position" value={draft.partial_close_percent} min={1} max={95}
              disabled={!draft.partial_close_enabled}
              onChange={(value) => setDraft({ ...draft, partial_close_percent: value })} />
          </div>

          <NumberField label="Maximum holding time" suffix="hours" value={draft.max_holding_hours} min={0} max={8760}
            help="0 disables the time stop"
            onChange={(value) => setDraft({ ...draft, max_holding_hours: value })} />

          <button className="btn primary" disabled={saving} onClick={() => save({
            break_even_enabled: draft.break_even_enabled,
            break_even_trigger_r: draft.break_even_trigger_r,
            break_even_offset_points: draft.break_even_offset_points,
            trailing_stop_enabled: draft.trailing_stop_enabled,
            trailing_start_r: draft.trailing_start_r,
            trailing_distance_atr: draft.trailing_distance_atr,
            partial_close_enabled: draft.partial_close_enabled,
            partial_close_at_r: draft.partial_close_at_r,
            partial_close_percent: draft.partial_close_percent,
            max_holding_hours: draft.max_holding_hours,
          })}>
            Save position management
          </button>
        </Card>
      </div>

      <Banner kind="info" title="What this bot will never do">
        No martingale, no grid, no averaging down and no increase of position size after a loss.
        Lot size is a pure function of balance, risk percentage and stop distance - the outcome of
        previous trades never enters the calculation.
      </Banner>
    </main>
  );
}
