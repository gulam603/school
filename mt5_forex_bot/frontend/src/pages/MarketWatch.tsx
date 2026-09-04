/** Live quote board for every configured symbol. */

import { useState } from 'react';
import { Card, Empty, Loading, Pill } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import type { LiveState } from '../hooks/useSnapshot';
import { api } from '../lib/api';
import { clockTime, percent } from '../lib/format';

export function MarketWatch({ live }: { live: LiveState }) {
  const [filter, setFilter] = useState('');
  const watch = useAsync(() => api.marketWatch(), [], { intervalMs: 2000 });
  const rows = (watch.data?.symbols ?? []).filter((row) =>
    row.symbol.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Market Watch</h1>
        <span className="page-sub">
          Quotes from {live.snapshot?.connection.broker || 'the connected broker'}
        </span>
        <span className="spacer" />
        <input
          type="text"
          placeholder="Filter symbols"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          style={{ width: 200 }}
        />
        {watch.loading && <span className="spinner" />}
      </div>

      <Card title="Symbols" bodyClass="flush">
        {watch.error ? (
          <Empty icon="!">{watch.error}</Empty>
        ) : !watch.data ? (
          <Loading label="Loading quotes" />
        ) : !rows.length ? (
          <Empty icon="--">No symbols match "{filter}"</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Broker symbol</th>
                  <th className="num">Bid</th>
                  <th className="num">Ask</th>
                  <th className="num">Spread</th>
                  <th className="num">Change</th>
                  <th className="num">Day high</th>
                  <th className="num">Day low</th>
                  <th>Status</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.symbol}>
                    <td><strong>{row.symbol}</strong></td>
                    <td className="faint mono">{row.broker_symbol ?? '--'}</td>
                    <td className="num">{row.bid?.toFixed(row.digits ?? 5) ?? '--'}</td>
                    <td className="num">{row.ask?.toFixed(row.digits ?? 5) ?? '--'}</td>
                    <td className="num faint">{row.spread_points ?? '--'} pts</td>
                    <td className={`num ${(row.change_percent ?? 0) >= 0 ? 'positive' : 'negative'}`}>
                      {row.change_percent != null ? percent(row.change_percent, 2, true) : '--'}
                    </td>
                    <td className="num faint">{row.day_high?.toFixed(row.digits ?? 5) ?? '--'}</td>
                    <td className="num faint">{row.day_low?.toFixed(row.digits ?? 5) ?? '--'}</td>
                    <td>
                      {row.error ? (
                        <Pill kind="bad">{row.error.slice(0, 40)}</Pill>
                      ) : (
                        <Pill kind={row.tradable ? 'ok' : 'warn'}>{row.tradable ? 'TRADABLE' : 'CLOSED'}</Pill>
                      )}
                    </td>
                    <td className="faint">{row.time ? clockTime(row.time) : '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </main>
  );
}
