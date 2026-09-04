/** Typed client for the local backend. Never holds credentials of any kind. */

import type {
  AccountInfo, BacktestResult, ChartPayload, ConnectionStatus, LogRecord, MarketRow,
  Position, RiskSummary, SettingsPayload, Snapshot, StrategyInfo, Trade, TradeResult,
} from './types';

const BASE = '/api';

export class ApiRequestError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch (cause) {
    throw new ApiRequestError(0, cause, 'Backend unreachable - is the server running?');
  }

  const text = await response.text();
  const body = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail = body?.detail ?? body;
    const message =
      typeof detail === 'string'
        ? detail
        : detail?.message ?? `Request failed with status ${response.status}`;
    throw new ApiRequestError(response.status, detail, message);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined });

export const api = {
  // -- system --------------------------------------------------------
  status: () => request<Snapshot>('/status'),
  health: () => request<{ status: string; demo_mode: boolean; connected: boolean }>('/health'),
  mode: () => request<{ demo: boolean; label: string; warning: string; live_unlocked: boolean }>('/mode'),
  connection: () => request<ConnectionStatus>('/connection'),
  terminals: () => request<{ detected: string[]; configured_path: string | null; mt5_package_available: boolean }>('/connection/terminals'),
  connect: (terminalPath?: string) =>
    post<{ connection: ConnectionStatus; account: AccountInfo }>('/connection/connect', { terminal_path: terminalPath ?? null }),
  disconnect: () => post<ConnectionStatus>('/connection/disconnect'),
  account: () => request<AccountInfo>('/account'),

  // -- settings ------------------------------------------------------
  settings: () => request<SettingsPayload>('/settings'),
  updateSettings: (changes: Record<string, unknown>) =>
    request<{ applied: string[]; config: SettingsPayload['config'] }>('/settings', {
      method: 'PUT',
      body: JSON.stringify(changes),
    }),
  symbolMapping: () =>
    request<{
      mapping: { symbol: string; broker_symbol: string | null; available: boolean; override: string | null; error: string | null }[];
      overrides: Record<string, string>;
      broker_symbols: string[];
      broker_symbol_count: number;
    }>('/settings/symbols'),
  setSymbolOverride: (symbol: string, brokerSymbol: string) =>
    post<{ overrides: Record<string, string> }>('/settings/symbols/override', { symbol, broker_symbol: brokerSymbol }),
  clearSymbolOverride: (symbol: string) =>
    request<{ overrides: Record<string, string> }>(`/settings/symbols/override/${symbol}`, { method: 'DELETE' }),

  // -- market --------------------------------------------------------
  marketWatch: () => request<{ symbols: MarketRow[]; timeframes: string[] }>('/market/watch'),
  candles: (symbol: string, timeframe: string, count = 300) =>
    request<ChartPayload>(`/market/candles/${symbol}?timeframe=${timeframe}&count=${count}`),
  indicators: (symbol: string, timeframe: string) =>
    request<{ symbol: string; timeframe: string; bar_time: string | null; values: Record<string, number | null> }>(
      `/market/indicators/${symbol}?timeframe=${timeframe}`,
    ),
  spec: (symbol: string) => request<Record<string, unknown>>(`/market/spec/${symbol}`),

  // -- bot -----------------------------------------------------------
  startBot: () => post<Snapshot['bot']>('/bot/start'),
  stopBot: () => post<Snapshot['bot']>('/bot/stop'),
  emergencyStop: () => post<{ status: Snapshot['bot']; close_all: { closed: number; failed: number; errors: string[] } }>('/bot/emergency-stop'),

  // -- positions & orders --------------------------------------------
  positions: (onlyBot = false) =>
    request<{ positions: Position[]; count: number; total_profit: number }>(`/positions?only_bot=${onlyBot}`),
  modifyPosition: (ticket: number, stopLoss?: number, takeProfit?: number) =>
    request<TradeResult>(`/positions/${ticket}`, {
      method: 'PUT',
      body: JSON.stringify({ stop_loss: stopLoss ?? null, take_profit: takeProfit ?? null }),
    }),
  closePosition: (ticket: number, volume?: number) =>
    post<TradeResult>(`/positions/${ticket}/close`, { volume: volume ?? null, reason: 'manual' }),
  closeAll: (onlyBot = false) =>
    post<{ closed: number; failed: number; errors: string[] }>('/positions/close-all', {
      only_bot_positions: onlyBot,
      reason: 'manual_close_all',
    }),
  pendingOrders: () => request<{ orders: Record<string, unknown>[]; count: number }>('/orders/pending'),
  orderLog: (limit = 100) => request<{ orders: Record<string, unknown>[] }>(`/orders/log?limit=${limit}`),

  // -- analysis ------------------------------------------------------
  trades: (limit = 200, symbol?: string) =>
    request<{ trades: Trade[]; statistics: Snapshot['statistics'] }>(
      `/history/trades?limit=${limit}${symbol ? `&symbol=${symbol}` : ''}`,
    ),
  deals: (days = 30) => request<{ deals: Record<string, unknown>[]; count: number }>(`/history/deals?days=${days}`),
  dailyPerformance: (limit = 60) => request<{ days: Record<string, unknown>[] }>(`/performance/daily?limit=${limit}`),
  signals: (limit = 50) => request<{ signals: Record<string, unknown>[] }>(`/signals?limit=${limit}`),
  strategyRuns: (limit = 20) => request<{ runs: Record<string, unknown>[] }>(`/strategy/runs?limit=${limit}`),
  strategies: () =>
    request<{ strategies: StrategyInfo[]; active: string; params: Record<string, unknown>; current_signals: Record<string, unknown> }>('/strategies'),
  risk: () => request<RiskSummary>('/risk'),
  logs: (limit = 200, category?: string) =>
    request<{ logs: LogRecord[]; categories: string[] }>(
      `/logs?limit=${limit}${category && category !== 'ALL' ? `&category=${category}` : ''}`,
    ),

  // -- backtest ------------------------------------------------------
  runBacktest: (payload: {
    symbol: string; timeframe: string; start_date: string; end_date: string;
    initial_balance: number; risk_percent: number; strategy?: string;
  }) => post<BacktestResult>('/backtest/run', payload),
  backtests: () => request<{ results: BacktestResult[]; disclaimer: string }>('/backtest/results'),
  backtest: (id: number) => request<BacktestResult>(`/backtest/results/${id}`),
  deleteBacktest: (id: number) => request<{ deleted: number }>(`/backtest/results/${id}`, { method: 'DELETE' }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
