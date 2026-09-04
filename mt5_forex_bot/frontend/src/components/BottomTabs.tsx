/** Dashboard bottom panel: recent trades, open positions, pending orders, logs. */

import { useState } from 'react';
import { useAsync } from '../hooks/useAsync';
import { api } from '../lib/api';
import { lots, price } from '../lib/format';
import type { LogRecord, Position, Trade } from '../lib/types';
import { LogStream, PositionsTable, TradesTable } from './tables';
import { Empty, Loading } from './ui';

const TABS = ['Recent Trades', 'Open Positions', 'Pending Orders', 'Logs'] as const;
type Tab = (typeof TABS)[number];

export function BottomTabs({
  trades, positions, logs,
}: { trades: Trade[]; positions: Position[]; logs: LogRecord[] }) {
  const [tab, setTab] = useState<Tab>('Recent Trades');
  const pending = useAsync(() => api.pendingOrders(), [], { immediate: tab === 'Pending Orders' });

  const counts: Record<Tab, number | undefined> = {
    'Recent Trades': trades.length,
    'Open Positions': positions.length,
    'Pending Orders': pending.data?.count,
    Logs: logs.length,
  };

  return (
    <section className="card">
      <div className="tabs">
        {TABS.map((name) => (
          <button
            key={name}
            className={`tab ${tab === name ? 'active' : ''}`}
            onClick={() => {
              setTab(name);
              if (name === 'Pending Orders') void pending.reload();
            }}
          >
            {name}
            {counts[name] !== undefined && <span className="tab-count">{counts[name]}</span>}
          </button>
        ))}
      </div>

      <div className="card-body flush" style={{ maxHeight: 320, overflowY: 'auto' }}>
        {tab === 'Recent Trades' && <TradesTable trades={trades} />}
        {tab === 'Open Positions' && <PositionsTable positions={positions} />}
        {tab === 'Pending Orders' && (
          pending.loading ? <Loading label="Loading orders" />
            : pending.error ? <Empty icon="!">{pending.error}</Empty>
            : !pending.data?.orders.length ? <Empty icon="--">No pending orders</Empty>
            : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Ticket</th><th>Symbol</th><th className="num">Volume</th>
                      <th className="num">Price</th><th className="num">SL</th><th className="num">TP</th><th>Comment</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pending.data.orders.map((order) => (
                      <tr key={String(order.ticket)}>
                        <td className="num faint">#{String(order.ticket)}</td>
                        <td><strong>{String(order.symbol)}</strong></td>
                        <td className="num">{lots(order.volume as number)}</td>
                        <td className="num">{price(order.price_open as number)}</td>
                        <td className="num faint">{price(order.stop_loss as number)}</td>
                        <td className="num faint">{price(order.take_profit as number)}</td>
                        <td className="faint">{String(order.comment ?? '')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
        )}
        {tab === 'Logs' && <LogStream logs={[...logs].slice(-200).reverse()} height={300} />}
      </div>
    </section>
  );
}
