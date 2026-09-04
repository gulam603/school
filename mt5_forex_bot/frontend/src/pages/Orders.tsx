/** Pending orders in the terminal plus the bot's own order request log. */

import { Card, Empty, Loading, Pill } from '../components/ui';
import { useAsync } from '../hooks/useAsync';
import { api } from '../lib/api';
import { lots, price, shortDateTime } from '../lib/format';

export function Orders() {
  const pending = useAsync(() => api.pendingOrders(), [], { intervalMs: 5000 });
  const log = useAsync(() => api.orderLog(150), [], { intervalMs: 8000 });

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Orders</h1>
        <span className="page-sub">Pending orders and every request the bot sent to MT5</span>
      </div>

      <Card title="Pending orders" bodyClass="flush">
        {pending.error ? <Empty icon="!">{pending.error}</Empty>
          : !pending.data ? <Loading />
          : !pending.data.orders.length ? <Empty icon="--">No pending orders</Empty>
          : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Ticket</th><th>Symbol</th><th className="num">Volume</th><th className="num">Price</th>
                    <th className="num">SL</th><th className="num">TP</th><th className="num">Magic</th><th>Comment</th>
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
                      <td className="num faint">{String(order.magic)}</td>
                      <td className="faint">{String(order.comment ?? '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      <Card title="Order request log" bodyClass="flush">
        {log.error ? <Empty icon="!">{log.error}</Empty>
          : !log.data ? <Loading />
          : !log.data.orders.length ? <Empty icon="--">The bot has not sent any orders yet</Empty>
          : (
            <div className="table-wrap" style={{ maxHeight: 460, overflowY: 'auto' }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Time</th><th>Action</th><th>Symbol</th><th className="num">Volume</th>
                    <th className="num">Price</th><th className="num">SL</th><th className="num">TP</th>
                    <th>Result</th><th>Mode</th>
                  </tr>
                </thead>
                <tbody>
                  {log.data.orders.map((order) => (
                    <tr key={String(order.id)}>
                      <td className="faint">{shortDateTime(String(order.created_at))}</td>
                      <td><Pill kind="muted">{String(order.action)}</Pill></td>
                      <td><strong>{String(order.symbol)}</strong></td>
                      <td className="num">{lots(order.volume as number)}</td>
                      <td className="num">{price(order.price as number)}</td>
                      <td className="num faint">{price(order.stop_loss as number)}</td>
                      <td className="num faint">{price(order.take_profit as number)}</td>
                      <td>
                        <Pill kind={order.success ? 'ok' : 'bad'}>
                          {order.success ? 'OK' : String(order.retcode_text ?? order.error_message ?? 'FAILED').slice(0, 42)}
                        </Pill>
                      </td>
                      <td className="faint">{String(order.mode)}</td>
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
