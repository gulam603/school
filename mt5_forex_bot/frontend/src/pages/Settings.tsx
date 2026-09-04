/**
 * Settings: MT5 connection, symbols, trading defaults, notifications and the
 * demo/live safety status.
 *
 * Credentials are deliberately absent from this page - the MT5 login, password
 * and Telegram token live in the backend's .env file and are never sent to the
 * browser. The page can only report whether they are configured.
 */

import { useEffect, useState } from 'react';
import { useToast } from '../components/Toasts';
import { Banner, Card, Empty, Loading, NumberField, Pill, SelectField, Switch } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import type { LiveState } from '../hooks/useSnapshot';
import { api, errorMessage } from '../lib/api';
import type { BotConfig } from '../lib/types';

const ALL_SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD', 'XAUUSD'];

export function SettingsPage({ live }: { live: LiveState }) {
  const toast = useToast();
  const settings = useAsync(() => api.settings(), []);
  const mapping = useAsync(() => api.symbolMapping(), [], { immediate: false });
  const [draft, setDraft] = useState<BotConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [terminalPath, setTerminalPath] = useState('');

  useEffect(() => {
    if (settings.data?.config) setDraft(settings.data.config);
  }, [settings.data]);

  useEffect(() => {
    if (live.snapshot?.connection.connected) void mapping.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.snapshot?.connection.connected]);

  const save = async (fields: Partial<BotConfig>) => {
    setSaving(true);
    try {
      const response = await api.updateSettings(fields as Record<string, unknown>);
      setDraft(response.config);
      toast.ok('Settings saved');
      await live.refresh();
    } catch (cause) {
      toast.bad('Could not save', errorMessage(cause));
    } finally {
      setSaving(false);
    }
  };

  const connect = async () => {
    setConnecting(true);
    try {
      const response = await api.connect(terminalPath || undefined);
      toast.ok(`Connected to account ${response.account.login}`, `${response.account.broker} - ${response.account.account_type}`);
      await live.refresh();
      void mapping.reload();
    } catch (cause) {
      toast.bad('Connection failed', errorMessage(cause));
    } finally {
      setConnecting(false);
    }
  };

  if (!draft || !settings.data) return <main className="page"><Loading label="Loading settings" /></main>;

  const environment = settings.data.environment as Record<string, unknown>;
  const connection = live.snapshot?.connection;
  const isDemo = live.snapshot?.mode.demo ?? true;

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Settings</h1>
        <span className="page-sub">Runtime configuration - credentials stay in the backend's .env file</span>
      </div>

      <Banner kind={isDemo ? 'ok' : 'bad'} title={isDemo ? 'DEMO MODE (default)' : 'LIVE TRADING UNLOCKED'}>
        {isDemo ? (
          <>
            The bot refuses to send orders to a real-money account. To enable live trading you must
            set <code>TRADING_MODE=live</code>, <code>ALLOW_LIVE_TRADING=true</code> and the exact
            confirmation phrase in <code>.env</code>, then restart the backend. There is no switch
            for it in this UI on purpose.
          </>
        ) : (
          <>Orders will use real money. Set <code>TRADING_MODE=demo</code> in .env and restart to return to safety.</>
        )}
      </Banner>

      <div className="grid-2">
        <Card title="MetaTrader 5 connection">
          <div className="row">
            <Pill kind={connection?.connected ? 'ok' : 'bad'}>
              {connection?.connected ? 'CONNECTED' : 'DISCONNECTED'}
            </Pill>
            {connection?.account_type && <Pill kind={connection.is_demo_account ? 'demo' : 'live'}>{connection.account_type}</Pill>}
            {connection?.terminal_build ? <span className="faint small">build {connection.terminal_build}</span> : null}
          </div>

          <div className="col small">
            <div className="kv"><span className="kv-key">Account</span><span className="kv-val">{connection?.account_number ?? '--'}</span></div>
            <div className="kv"><span className="kv-key">Server</span><span className="kv-val">{connection?.server || '--'}</span></div>
            <div className="kv"><span className="kv-key">Broker</span><span className="kv-val">{connection?.broker || '--'}</span></div>
            <div className="kv"><span className="kv-key">Terminal</span><span className="kv-val" style={{ fontSize: 10 }}>{connection?.terminal_path ?? 'auto'}</span></div>
            <div className="kv"><span className="kv-key">Credentials in .env</span>
              <span className="kv-val">{environment.mt5_login_configured ? 'yes' : 'terminal session'}</span></div>
          </div>

          <div className="field">
            <label className="field-label">Terminal path override (optional)</label>
            <select value={terminalPath} onChange={(event) => setTerminalPath(event.target.value)}>
              <option value="">Auto-detect / use .env</option>
              {settings.data.terminal_paths.map((path) => <option key={path} value={path}>{path}</option>)}
            </select>
          </div>

          <div className="row">
            <button className="btn primary" disabled={connecting} onClick={connect}>
              {connecting ? <span className="spinner" /> : null} Connect
            </button>
            <button
              className="btn"
              disabled={!connection?.connected}
              onClick={async () => { await api.disconnect(); await live.refresh(); toast.info('Disconnected'); }}
            >
              Disconnect
            </button>
          </div>
          {connection?.last_error && <span className="small negative">{connection.last_error}</span>}
        </Card>

        <Card title="Trading defaults">
          <SelectField
            label="Default symbol"
            value={draft.symbol}
            options={draft.traded_symbols.map((symbol) => ({ value: symbol, label: symbol }))}
            onChange={(value) => setDraft({ ...draft, symbol: value })}
          />
          <SelectField
            label="Timeframe"
            value={draft.timeframe}
            options={settings.data.timeframes.map((tf) => ({ value: tf, label: tf }))}
            onChange={(value) => setDraft({ ...draft, timeframe: value })}
          />
          <div className="field">
            <label className="field-label">Traded symbols</label>
            <div className="row tight">
              {ALL_SYMBOLS.map((symbol) => {
                const active = draft.traded_symbols.includes(symbol);
                return (
                  <button
                    key={symbol}
                    className={`btn sm ${active ? 'primary' : 'ghost'}`}
                    onClick={() =>
                      setDraft({
                        ...draft,
                        traded_symbols: active
                          ? draft.traded_symbols.filter((item) => item !== symbol)
                          : [...draft.traded_symbols, symbol],
                      })
                    }
                  >
                    {symbol}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="grid-2">
            <div className="field">
              <label className="field-label">Broker symbol prefix</label>
              <input type="text" value={draft.symbol_prefix} placeholder="e.g. FX_"
                onChange={(event) => setDraft({ ...draft, symbol_prefix: event.target.value })} />
            </div>
            <div className="field">
              <label className="field-label">Broker symbol suffix</label>
              <input type="text" value={draft.symbol_suffix} placeholder="e.g. m or .a"
                onChange={(event) => setDraft({ ...draft, symbol_suffix: event.target.value })} />
            </div>
          </div>
          <div className="grid-3">
            <NumberField label="Magic number" value={draft.magic_number} min={1}
              onChange={(value) => setDraft({ ...draft, magic_number: value })} />
            <NumberField label="Slippage" suffix="points" value={draft.deviation_points} min={0} max={1000}
              onChange={(value) => setDraft({ ...draft, deviation_points: value })} />
            <NumberField label="Poll interval" suffix="s" value={draft.poll_interval_seconds} min={1} max={3600}
              onChange={(value) => setDraft({ ...draft, poll_interval_seconds: value })} />
          </div>
          <button className="btn primary" disabled={saving} onClick={() => save({
            symbol: draft.symbol,
            timeframe: draft.timeframe,
            traded_symbols: draft.traded_symbols,
            symbol_prefix: draft.symbol_prefix,
            symbol_suffix: draft.symbol_suffix,
            magic_number: draft.magic_number,
            deviation_points: draft.deviation_points,
            poll_interval_seconds: draft.poll_interval_seconds,
          })}>
            Save trading defaults
          </button>
        </Card>
      </div>

      <Card
        title="Broker symbol mapping"
        action={<button className="btn sm ghost" onClick={() => void mapping.reload()}>Refresh</button>}
        bodyClass="flush"
      >
        {!connection?.connected ? (
          <Empty icon="--">Connect to MetaTrader 5 to resolve broker symbol names</Empty>
        ) : mapping.loading ? (
          <Loading label="Resolving symbols" />
        ) : mapping.error ? (
          <Empty icon="!">{mapping.error}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Symbol</th><th>Broker symbol</th><th>Status</th><th>Override</th><th /></tr>
              </thead>
              <tbody>
                {(mapping.data?.mapping ?? []).map((row) => (
                  <tr key={row.symbol}>
                    <td><strong>{row.symbol}</strong></td>
                    <td className="mono">{row.broker_symbol ?? '--'}</td>
                    <td>
                      <Pill kind={row.available ? 'ok' : 'bad'}>
                        {row.available ? 'RESOLVED' : (row.error ?? 'NOT FOUND').slice(0, 44)}
                      </Pill>
                    </td>
                    <td className="faint mono">{row.override ?? '--'}</td>
                    <td>
                      <div className="row tight">
                        <select
                          defaultValue=""
                          onChange={async (event) => {
                            if (!event.target.value) return;
                            try {
                              await api.setSymbolOverride(row.symbol, event.target.value);
                              toast.ok(`${row.symbol} mapped to ${event.target.value}`);
                              void mapping.reload();
                            } catch (cause) {
                              toast.bad('Override failed', errorMessage(cause));
                            }
                          }}
                          style={{ width: 190 }}
                        >
                          <option value="">Map manually...</option>
                          {(mapping.data?.broker_symbols ?? [])
                            .filter((name) => name.toUpperCase().includes(row.symbol.slice(0, 3)))
                            .slice(0, 60)
                            .map((name) => <option key={name} value={name}>{name}</option>)}
                        </select>
                        {row.override && (
                          <button
                            className="btn sm ghost"
                            onClick={async () => {
                              await api.clearSymbolOverride(row.symbol);
                              void mapping.reload();
                            }}
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid-2">
        <Card title="Notifications">
          <Switch
            checked={draft.telegram_notifications}
            disabled={!settings.data.notifications.configured}
            label="Telegram notifications"
            onChange={(value) => { setDraft({ ...draft, telegram_notifications: value }); void save({ telegram_notifications: value }); }}
          />
          <div className="col small faint">
            <div className="kv">
              <span className="kv-key">Credentials configured</span>
              <span className="kv-val">{settings.data.notifications.configured ? 'yes' : 'no'}</span>
            </div>
            <div className="kv">
              <span className="kv-key">Messages sent</span>
              <span className="kv-val">{settings.data.notifications.messages_sent}</span>
            </div>
            {settings.data.notifications.last_error && (
              <span className="negative">{settings.data.notifications.last_error}</span>
            )}
          </div>
          {!settings.data.notifications.configured && (
            <Banner kind="info">
              Set <code>TELEGRAM_BOT_TOKEN</code> and <code>TELEGRAM_CHAT_ID</code> in
              {' '}<code>.env</code> and restart the backend to enable notifications.
            </Banner>
          )}
        </Card>

        <Card title="Environment">
          <div className="col small">
            {Object.entries(environment).map(([key, value]) => (
              <div className="kv" key={key}>
                <span className="kv-key">{key.replace(/_/g, ' ')}</span>
                <span className="kv-val">{typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value || '--')}</span>
              </div>
            ))}
          </div>
          <span className="faint small">
            Read-only. Edit <code>.env</code> next to the backend and restart to change these.
          </span>
        </Card>
      </div>
    </main>
  );
}
