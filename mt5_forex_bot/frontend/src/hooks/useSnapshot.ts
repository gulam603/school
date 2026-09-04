/**
 * Live dashboard state.
 *
 * Primary transport is the backend WebSocket (snapshots once a second plus
 * event pushes). If the socket cannot be established, or drops, the hook falls
 * back to polling /api/status and keeps trying to reconnect, so the dashboard
 * degrades instead of going blank.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { LogRecord, Snapshot } from '../lib/types';

const MAX_LIVE_LOGS = 300;

export interface LiveState {
  snapshot: Snapshot | null;
  logs: LogRecord[];
  connected: boolean;
  transport: 'websocket' | 'polling' | 'offline';
  error: string | null;
  refresh: () => Promise<void>;
}

export function useSnapshot(): LiveState {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [transport, setTransport] = useState<LiveState['transport']>('offline');
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<number | null>(null);
  const retryRef = useRef(0);
  const closedRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api.status();
      setSnapshot(next);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current !== null) return;
    setTransport('polling');
    void refresh();
    pollRef.current = window.setInterval(refresh, 3000);
  }, [refresh]);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (closedRef.current) return;
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    let socket: WebSocket;
    try {
      socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
    } catch {
      startPolling();
      return;
    }
    socketRef.current = socket;

    socket.onopen = () => {
      retryRef.current = 0;
      stopPolling();
      setTransport('websocket');
      setError(null);
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as { type: string; payload?: unknown };
        if (message.type === 'snapshot') {
          setSnapshot(message.payload as Snapshot);
        } else if (message.type === 'log') {
          setLogs((previous) => [...previous, message.payload as LogRecord].slice(-MAX_LIVE_LOGS));
        }
      } catch {
        /* ignore malformed frames */
      }
    };

    socket.onerror = () => { startPolling(); };

    socket.onclose = () => {
      socketRef.current = null;
      if (closedRef.current) return;
      startPolling();
      retryRef.current += 1;
      const delay = Math.min(15000, 1000 * 2 ** Math.min(retryRef.current, 4));
      window.setTimeout(connect, delay);
    };
  }, [startPolling, stopPolling]);

  useEffect(() => {
    closedRef.current = false;
    void refresh();
    connect();
    const keepalive = window.setInterval(() => {
      if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send('ping');
    }, 25000);

    return () => {
      closedRef.current = true;
      window.clearInterval(keepalive);
      stopPolling();
      socketRef.current?.close();
    };
  }, [connect, refresh, stopPolling]);

  // Seed the log panel with history so it is not empty on first paint.
  useEffect(() => {
    api
      .logs(120)
      .then((response) => setLogs((live) => (live.length ? live : [...response.logs].reverse())))
      .catch(() => undefined);
  }, []);

  return {
    snapshot,
    logs,
    connected: snapshot?.connection.connected ?? false,
    transport,
    error,
    refresh,
  };
}
