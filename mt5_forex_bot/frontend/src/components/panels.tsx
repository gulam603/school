/** Right-hand dashboard panels: bot status, strategy, risk and the controls. */

import { useState } from 'react';
import { api, errorMessage } from '../lib/api';
import { duration, money, percent, price, shortDateTime } from '../lib/format';
import type { Snapshot } from '../lib/types';
import { useToast } from './Toasts';
import { Banner, Card, KeyValue, Meter, Modal, Pill } from './ui';

export function BotStatusPanel({ snapshot }: { snapshot: Snapshot | null }) {
  const bot = snapshot?.bot;
  const running = bot?.running ?? false;
  const lastSignal = bot?.last_signal;

  return (
    <Card title="Bot Status" bodyClass="tight">
      <div className="row">
        <Pill kind={running ? 'ok' : 'muted'}>
          <span className={`dot ${running ? 'blink' : ''}`} />
          {running ? 'RUNNING' : 'STOPPED'}
        </Pill>
        {bot?.daily_limit_blocked && <Pill kind="bad">DAILY LIMIT</Pill>}
      </div>
      <KeyValue label="Uptime" value={duration(bot?.uptime_seconds)} />
      <KeyValue label="Last tick" value={bot?.last_tick ? shortDateTime(bot.last_tick) : '--'} />
      <KeyValue label="Signals / Trades" value={`${bot?.signals ?? 0} / ${bot?.trades ?? 0}`} />
      <KeyValue
        label="Last signal"
        value={
          lastSignal ? (
            <span className={lastSignal.direction === 'BUY' ? 'positive' : 'negative'}>
              {lastSignal.symbol} {lastSignal.direction}
            </span>
          ) : (
            '--'
          )
        }
      />
      <KeyValue
        label="Last trade"
        value={bot?.last_trade ? `${bot.last_trade.symbol} #${bot.last_trade.ticket ?? '--'}` : '--'}
      />
      {bot?.last_error && (
        <div className="small negative" style={{ wordBreak: 'break-word' }}>{bot.last_error}</div>
      )}
    </Card>
  );
}

export function StrategyPanel({ snapshot }: { snapshot: Snapshot | null }) {
  const bot = snapshot?.bot;
  const active = bot?.symbols?.[0] ?? '--';
  const signal = bot?.current_signals?.[bot?.symbols?.[0] ?? ''];

  return (
    <Card title="Strategy">
      <div>
        <div style={{ fontSize: 15, fontWeight: 650 }}>
          {(bot?.strategy ?? 'ema_crossover').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
        </div>
        <div className="faint small">{bot?.symbols?.length ?? 0} symbols watched</div>
      </div>
      <KeyValue label="Primary symbol" value={active} />
      <KeyValue label="Timeframe" value={bot?.timeframe ?? '--'} />
      <KeyValue
        label="Current signal"
        value={
          signal ? (
            <span className={signal.direction === 'BUY' ? 'positive' : signal.direction === 'SELL' ? 'negative' : 'neutral'}>
              {signal.direction}
            </span>
          ) : (
            'NONE'
          )
        }
      />
      {signal?.reason && <div className="small faint" style={{ lineHeight: 1.45 }}>{signal.reason}</div>}
      {signal?.indicators && (
        <div className="row tight small faint">
          <span>EMA {price(signal.indicators.ema_fast as number, 5)}</span>
          <span>/</span>
          <span>{price(signal.indicators.ema_slow as number, 5)}</span>
          {signal.indicators.rsi != null && <span>- RSI {(signal.indicators.rsi as number).toFixed(1)}</span>}
        </div>
      )}
    </Card>
  );
}

export function RiskPanel({ snapshot }: { snapshot: Snapshot | null }) {
  const risk = snapshot?.risk as Snapshot['risk'] & { daily?: Snapshot['risk'] extends never ? never : any };
  const daily = (risk as any)?.daily;
  const limit = (risk as any)?.max_daily_loss_percent ?? 5;
  const used = daily?.loss_percent ?? 0;
  const tone = used >= limit ? 'bad' : used >= limit * 0.6 ? 'warn' : 'ok';
  const maxTrades = (risk as any)?.max_open_trades ?? 5;
  const openTrades = (risk as any)?.open_trades ?? 0;

  return (
    <Card title="Risk Management" bodyClass="tight">
      <KeyValue label="Risk per trade" value={percent((risk as any)?.risk_per_trade_percent ?? 1)} />
      <KeyValue label="Daily loss limit" value={percent(limit)} />
      <KeyValue label="Current daily loss" value={<span className={tone === 'bad' ? 'negative' : ''}>{percent(used)}</span>} />
      <Meter value={used} max={limit} tone={tone} />
      <KeyValue label="Max open trades" value={maxTrades} />
      <KeyValue label="Open trades" value={`${openTrades} / ${maxTrades}`} />
      <Meter value={openTrades} max={maxTrades} tone="accent" />
      <KeyValue label="Min R:R" value={`1:${((risk as any)?.min_risk_reward ?? 2).toFixed(1)}`} />
      {daily?.limit_hit && (
        <Banner kind="bad" title="Daily loss limit reached">
          No new trades will be opened today. Open positions are still being managed.
        </Banner>
      )}
    </Card>
  );
}

export function TradingControls({
  snapshot, onChanged,
}: { snapshot: Snapshot | null; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | 'close-all' | 'emergency'>(null);

  const running = snapshot?.bot.running ?? false;
  const connected = snapshot?.connection.connected ?? false;
  const openCount = snapshot?.open_positions ?? 0;

  const act = async (name: string, action: () => Promise<unknown>, success: string) => {
    setBusy(name);
    try {
      await action();
      toast.ok(success);
      onChanged();
    } catch (cause) {
      toast.bad('Command failed', errorMessage(cause));
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  };

  return (
    <Card title="Trading Controls">
      <button
        className="btn success block"
        disabled={running || busy !== null || !connected}
        onClick={() => act('start', api.startBot, 'Bot started')}
      >
        {busy === 'start' ? <span className="spinner" /> : '>'} START BOT
      </button>

      <button
        className="btn danger block"
        disabled={!running || busy !== null}
        onClick={() => act('stop', api.stopBot, 'Bot stopped')}
      >
        {busy === 'stop' ? <span className="spinner" /> : '#'} STOP BOT
      </button>

      <button
        className="btn block"
        disabled={busy !== null || !connected || openCount === 0}
        onClick={() => setConfirm('close-all')}
      >
        x CLOSE ALL POSITIONS {openCount > 0 && `(${openCount})`}
      </button>

      <button
        className="btn outline-danger block"
        disabled={busy !== null || !connected}
        onClick={() => setConfirm('emergency')}
      >
        !! EMERGENCY STOP
      </button>

      {!connected && <div className="small faint center">Connect to MetaTrader 5 to enable trading</div>}

      {confirm === 'close-all' && (
        <Modal
          title="Close all positions?"
          onClose={() => setConfirm(null)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setConfirm(null)}>Cancel</button>
              <button
                className="btn danger"
                onClick={() => act('close-all', () => api.closeAll(false), 'Close-all sent')}
              >
                Close {openCount} position{openCount === 1 ? '' : 's'}
              </button>
            </>
          }
        >
          Every open position on this account will be closed at market price, including any you
          opened manually in MetaTrader 5. The bot keeps running.
        </Modal>
      )}

      {confirm === 'emergency' && (
        <Modal
          title="Emergency stop?"
          onClose={() => setConfirm(null)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setConfirm(null)}>Cancel</button>
              <button
                className="btn danger"
                onClick={() => act('emergency', api.emergencyStop, 'Emergency stop executed')}
              >
                Stop everything
              </button>
            </>
          }
        >
          <Banner kind="bad" title="This stops the bot and flattens the account">
            The strategy engine is stopped and every open position is closed at market price.
            Use this when something is clearly wrong.
          </Banner>
        </Modal>
      )}
    </Card>
  );
}

export function AccountSummary({ snapshot }: { snapshot: Snapshot | null }) {
  const account = snapshot?.account;
  if (!account) return null;
  return (
    <Card title="Account">
      <KeyValue label="Account" value={account.login} />
      <KeyValue label="Type" value={<Pill kind={account.is_demo ? 'demo' : 'live'}>{account.account_type}</Pill>} />
      <KeyValue label="Broker" value={account.broker} />
      <KeyValue label="Server" value={account.server} />
      <KeyValue label="Leverage" value={`1:${account.leverage}`} />
      <KeyValue label="Free margin" value={money(account.free_margin, account.currency)} />
      <KeyValue label="Margin level" value={account.margin_level ? percent(account.margin_level) : '--'} />
    </Card>
  );
}
