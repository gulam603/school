/** Open positions with manual SL/TP editing and close controls. */

import { useState } from 'react';
import { PositionsTable } from '../components/tables';
import { useToast } from '../components/Toasts';
import { Banner, Card, Modal, Stat } from '../components/ui';
import type { LiveState } from '../hooks/useSnapshot';
import { api, errorMessage } from '../lib/api';
import { money, signedMoney } from '../lib/format';
import type { Position } from '../lib/types';

export function PositionsPage({ live }: { live: LiveState }) {
  const toast = useToast();
  const positions = live.snapshot?.positions ?? [];
  const [busyTicket, setBusyTicket] = useState<number | null>(null);
  const [editing, setEditing] = useState<Position | null>(null);
  const [sl, setSl] = useState('');
  const [tp, setTp] = useState('');

  const totalProfit = positions.reduce((sum, row) => sum + row.profit + row.swap, 0);
  const totalVolume = positions.reduce((sum, row) => sum + row.volume, 0);

  const closeOne = async (ticket: number) => {
    setBusyTicket(ticket);
    try {
      await api.closePosition(ticket);
      toast.ok(`Position #${ticket} closed`);
      await live.refresh();
    } catch (cause) {
      toast.bad('Close failed', errorMessage(cause));
    } finally {
      setBusyTicket(null);
    }
  };

  const startEdit = (position: Position) => {
    setEditing(position);
    setSl(position.stop_loss ? String(position.stop_loss) : '');
    setTp(position.take_profit ? String(position.take_profit) : '');
  };

  const saveLevels = async () => {
    if (!editing) return;
    setBusyTicket(editing.ticket);
    try {
      await api.modifyPosition(editing.ticket, sl ? Number(sl) : undefined, tp ? Number(tp) : undefined);
      toast.ok(`SL/TP updated on #${editing.ticket}`);
      setEditing(null);
      await live.refresh();
    } catch (cause) {
      toast.bad('Modify failed', errorMessage(cause));
    } finally {
      setBusyTicket(null);
    }
  };

  return (
    <main className="page">
      <div className="page-head">
        <h1 className="page-title">Positions</h1>
        <span className="page-sub">Everything open on the connected account</span>
      </div>

      <div className="grid-4">
        <Stat label="Open positions" value={positions.length} />
        <Stat label="Total volume" value={`${totalVolume.toFixed(2)} lots`} />
        <Stat
          label="Floating P/L"
          value={signedMoney(totalProfit)}
          tone={totalProfit > 0 ? 'positive' : totalProfit < 0 ? 'negative' : ''}
        />
        <Stat label="Bot positions" value={positions.filter((row) => row.is_bot_position).length} />
      </div>

      {!live.snapshot?.connection.connected && (
        <Banner kind="warn" title="Not connected">Connect to MetaTrader 5 to see live positions.</Banner>
      )}

      <Card
        title="Open positions"
        bodyClass="flush"
        action={
          <button
            className="btn sm outline-danger"
            disabled={!positions.length}
            onClick={async () => {
              try {
                const result = await api.closeAll(false);
                toast.ok(`Closed ${result.closed} position(s)`, result.failed ? `${result.failed} failed` : undefined);
                await live.refresh();
              } catch (cause) {
                toast.bad('Close all failed', errorMessage(cause));
              }
            }}
          >
            Close all
          </button>
        }
      >
        <PositionsTable
          positions={positions}
          onClose={closeOne}
          onModify={startEdit}
          busyTicket={busyTicket}
        />
      </Card>

      {editing && (
        <Modal
          title={`Modify #${editing.ticket} - ${editing.symbol} ${editing.direction}`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn primary" disabled={busyTicket !== null} onClick={saveLevels}>
                Save levels
              </button>
            </>
          }
        >
          <div className="row">
            <span className="muted">Entry</span>
            <strong className="mono">{editing.entry_price}</strong>
            <span className="muted">Current</span>
            <strong className="mono">{editing.current_price}</strong>
            <span className="muted">P/L</span>
            <strong className={editing.profit >= 0 ? 'positive' : 'negative'}>{money(editing.profit)}</strong>
          </div>
          <div className="grid-2">
            <div className="field">
              <label className="field-label">Stop loss</label>
              <input type="number" step="0.00001" value={sl} onChange={(event) => setSl(event.target.value)} />
            </div>
            <div className="field">
              <label className="field-label">Take profit</label>
              <input type="number" step="0.00001" value={tp} onChange={(event) => setTp(event.target.value)} />
            </div>
          </div>
          <span className="faint small">
            Levels must respect the broker's minimum stop distance, otherwise MT5 rejects the change.
          </span>
        </Modal>
      )}
    </main>
  );
}
