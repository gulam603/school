/** Reusable tables: trades, positions and the log stream. */

import { lots, money, price, shortDateTime, signedMoney } from '../lib/format';
import type { LogRecord, Position, Trade } from '../lib/types';
import { DirectionTag, Empty, Pill } from './ui';

export function TradesTable({ trades, showStrategy = true }: { trades: Trade[]; showStrategy?: boolean }) {
  if (!trades.length) return <Empty icon="--">No trades recorded yet</Empty>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>Type</th>
            <th className="num">Lot</th>
            <th className="num">Entry</th>
            <th className="num">SL</th>
            <th className="num">TP</th>
            <th className="num">Exit</th>
            <th className="num">P/L</th>
            {showStrategy && <th>Strategy</th>}
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr key={trade.id ?? trade.ticket}>
              <td className="faint">{shortDateTime(trade.closed_at ?? trade.opened_at)}</td>
              <td><strong>{trade.symbol}</strong></td>
              <td><DirectionTag direction={trade.direction} /></td>
              <td className="num">{lots(trade.volume)}</td>
              <td className="num">{price(trade.entry_price)}</td>
              <td className="num faint">{price(trade.stop_loss)}</td>
              <td className="num faint">{price(trade.take_profit)}</td>
              <td className="num">{price(trade.exit_price)}</td>
              <td className={`num ${trade.profit > 0 ? 'positive' : trade.profit < 0 ? 'negative' : 'neutral'}`}>
                {trade.status === 'closed' ? signedMoney(trade.profit + trade.swap + trade.commission) : '--'}
              </td>
              {showStrategy && <td className="faint">{trade.strategy ?? '--'}</td>}
              <td>
                <Pill kind={trade.status === 'open' ? 'warn' : trade.close_reason === 'TP' ? 'ok' : 'muted'}>
                  {trade.status === 'open' ? 'OPEN' : trade.close_reason ?? 'CLOSED'}
                </Pill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PositionsTable({
  positions, onClose, onModify, busyTicket,
}: {
  positions: Position[];
  onClose?: (ticket: number) => void;
  onModify?: (position: Position) => void;
  busyTicket?: number | null;
}) {
  if (!positions.length) return <Empty icon="--">No open positions</Empty>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Ticket</th>
            <th>Symbol</th>
            <th>Type</th>
            <th className="num">Lot</th>
            <th className="num">Entry</th>
            <th className="num">Current</th>
            <th className="num">SL</th>
            <th className="num">TP</th>
            <th className="num">R</th>
            <th className="num">Swap</th>
            <th className="num">P/L</th>
            <th>Source</th>
            {(onClose || onModify) && <th />}
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.ticket}>
              <td className="num faint">#{position.ticket}</td>
              <td><strong>{position.symbol}</strong></td>
              <td><DirectionTag direction={position.direction} /></td>
              <td className="num">{lots(position.volume)}</td>
              <td className="num">{price(position.entry_price)}</td>
              <td className="num">{price(position.current_price)}</td>
              <td className="num faint">{price(position.stop_loss)}</td>
              <td className="num faint">{price(position.take_profit)}</td>
              <td className={`num ${(position.r_multiple ?? 0) >= 0 ? 'positive' : 'negative'}`}>
                {position.r_multiple != null ? `${position.r_multiple.toFixed(2)}R` : '--'}
              </td>
              <td className="num faint">{money(position.swap)}</td>
              <td className={`num ${position.profit > 0 ? 'positive' : position.profit < 0 ? 'negative' : 'neutral'}`}>
                {signedMoney(position.profit)}
              </td>
              <td>
                <Pill kind={position.is_bot_position ? 'ok' : 'muted'}>
                  {position.is_bot_position ? 'BOT' : 'MANUAL'}
                </Pill>
              </td>
              {(onClose || onModify) && (
                <td>
                  <div className="row tight">
                    {onModify && (
                      <button className="btn sm ghost" onClick={() => onModify(position)}>Edit SL/TP</button>
                    )}
                    {onClose && (
                      <button
                        className="btn sm outline-danger"
                        disabled={busyTicket === position.ticket}
                        onClick={() => onClose(position.ticket)}
                      >
                        {busyTicket === position.ticket ? <span className="spinner" /> : 'Close'}
                      </button>
                    )}
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LogStream({ logs, height = 260 }: { logs: LogRecord[]; height?: number | string }) {
  if (!logs.length) return <Empty icon="--">No log entries yet</Empty>;
  return (
    <div className="log-stream" style={{ maxHeight: height }}>
      {logs.map((log, index) => (
        <div className="log-line" key={log.id ?? `${log.timestamp}-${index}`}>
          <span className="log-time">{new Date(log.timestamp).toLocaleTimeString('en-GB', { hour12: false })}</span>
          <span className={`log-cat cat-${log.category}`}>{log.category}</span>
          <span className="log-msg">
            {log.symbol ? <strong style={{ color: '#c4b5fd' }}>{log.symbol} </strong> : null}
            {log.message}
          </span>
        </div>
      ))}
    </div>
  );
}
