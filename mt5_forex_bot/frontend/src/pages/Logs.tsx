/** The structured log stream with category filtering. */

import { useState } from 'react';
import { LogStream } from '../components/tables';
import { Card, Empty, Loading } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import type { LiveState } from '../hooks/useSnapshot';
import { api } from '../lib/api';

const CATEGORIES = ['ALL', 'INFO', 'SIGNAL', 'TRADE', 'RISK', 'WARNING', 'ERROR'];

export function Logs({ live }: { live: LiveState }) {
  const [category, setCategory] = useState('ALL');
  const [followLive, setFollowLive] = useState(true);
  const stored = useAsync(() => api.logs(500, category), [category], { intervalMs: followLive ? 0 : 10000 });

  const liveLogs = category === 'ALL' ? live.logs : live.logs.filter((log) => log.category === category);
  const records = followLive ? [...liveLogs].reverse() : (stored.data?.logs ?? []);

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Logs</h1>
        <span className="page-sub">
          {followLive ? 'Streaming live from the bot' : 'Loaded from the database'}
        </span>
        <span className="spacer" />
        <div className="tf-group">
          {CATEGORIES.map((item) => (
            <button
              key={item}
              className={`tf-btn ${category === item ? 'active' : ''}`}
              onClick={() => setCategory(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <button className="btn sm" onClick={() => { setFollowLive(!followLive); if (followLive) void stored.reload(); }}>
          {followLive ? 'Show history' : 'Follow live'}
        </button>
      </div>

      <Card title={`${records.length} entries`} bodyClass="flush">
        {!followLive && stored.loading ? <Loading />
          : stored.error && !followLive ? <Empty icon="!">{stored.error}</Empty>
          : <LogStream logs={records} height="calc(100vh - 230px)" />}
      </Card>
    </main>
  );
}
