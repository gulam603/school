/** Top bar: mode indicator, account, broker, connection status and the clock. */

import { useEffect, useState } from 'react';
import { clockTime } from '../lib/format';
import type { Snapshot } from '../lib/types';
import { Pill } from './ui';

export function TopBar({
  snapshot, transport, title,
}: {
  snapshot: Snapshot | null; transport: string; title: string;
}) {
  const [now, setNow] = useState(() => clockTime());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(clockTime()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const connection = snapshot?.connection;
  const account = snapshot?.account;
  const isDemo = snapshot?.mode.demo ?? true;
  const connected = connection?.connected ?? false;

  return (
    <header className="topbar">
      <span className="topbar-title">{title}</span>

      <Pill kind={isDemo ? 'demo' : 'live'}>
        {isDemo ? 'DEMO' : 'LIVE'}<span className="conn-text">&nbsp;{isDemo ? 'MODE' : 'TRADING'}</span>
      </Pill>

      {account && (
        <span className="topbar-meta hide-sm mono">
          {account.login} - {account.broker || account.server}
        </span>
      )}

      <span className="topbar-spacer" />

      {snapshot && !isDemo && (
        <Pill kind="bad">REAL MONEY</Pill>
      )}

      <span className="topbar-meta">
        <Pill kind={connected ? 'ok' : 'bad'}>
          <span className={`dot ${connected ? 'blink' : ''}`} />
          {/* On narrow screens the dot alone carries the state. */}
          <span className="conn-text">{connected ? 'Connected' : 'Disconnected'}</span>
        </Pill>
      </span>

      <span className="topbar-meta hide-sm faint small" title={`Live updates via ${transport}`}>
        {transport === 'websocket' ? 'WS' : transport === 'polling' ? 'HTTP' : 'OFF'}
      </span>

      <span className="topbar-clock">{now}</span>
    </header>
  );
}
