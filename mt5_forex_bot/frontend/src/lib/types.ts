/** Shared shapes returned by the local FastAPI backend. */

export type Direction = 'BUY' | 'SELL' | 'NONE';

export interface ConnectionStatus {
  connected: boolean;
  mt5_package_available: boolean;
  terminal_path: string | null;
  account_number: number | null;
  server: string;
  broker: string;
  account_type: string | null;
  is_demo_account: boolean | null;
  demo_mode: boolean;
  live_trading_unlocked: boolean;
  connection_uptime_seconds: number;
  connected_since: string | null;
  last_error: string;
  reconnect_attempts: number;
  server_time: string;
  terminal_build?: number;
  terminal_trade_allowed?: boolean;
  ping_ms?: number;
}

export interface AccountInfo {
  login: number;
  name: string;
  server: string;
  broker: string;
  currency: string;
  leverage: number;
  balance: number;
  equity: number;
  margin: number;
  free_margin: number;
  margin_level: number;
  profit: number;
  trade_mode: number;
  account_type: string;
  is_demo: boolean;
  trade_allowed: boolean;
  trade_expert: boolean;
}

export interface SignalPayload {
  symbol: string;
  timeframe: string;
  direction: Direction;
  price: number | null;
  reason: string;
  strategy: string;
  atr: number | null;
  bar_time: string | null;
  indicators: Record<string, number | null>;
}

export interface BotStatus {
  running: boolean;
  started_at: string | null;
  uptime_seconds: number;
  strategy: string;
  symbols: string[];
  timeframe: string;
  mode: 'demo' | 'live';
  demo_mode: boolean;
  signals: number;
  trades: number;
  last_signal: SignalPayload | null;
  last_trade: TradeResult | null;
  current_signals: Record<string, SignalPayload>;
  last_tick: string | null;
  last_error: string;
  stop_reason: string;
  daily_limit_blocked: boolean;
}

export interface TradeResult {
  success: boolean;
  action: string;
  symbol: string;
  retcode: number | null;
  retcode_text: string;
  message: string;
  ticket: number | null;
  volume: number;
  price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
}

export interface DailyStatus {
  day: string;
  starting_balance: number;
  balance: number;
  equity: number;
  realized_pnl: number;
  floating_pnl: number;
  total_pnl: number;
  loss_percent: number;
  limit_percent: number;
  limit_hit: boolean;
  remaining_percent: number;
  trades_today: number;
  wins_today: number;
  losses_today: number;
}

export interface RiskSummary {
  risk_per_trade_percent: number;
  max_daily_loss_percent: number;
  max_open_trades: number;
  open_trades: number;
  min_risk_reward: number;
  atr_period: number;
  atr_sl_multiplier: number;
  max_lot_size: number;
  max_spread_points: number;
  daily: DailyStatus | null;
  error: string | null;
}

export interface Position {
  ticket: number;
  symbol: string;
  broker_symbol: string;
  direction: 'BUY' | 'SELL';
  volume: number;
  entry_price: number;
  current_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  initial_sl: number | null;
  profit: number;
  swap: number;
  commission: number;
  opened_at: string;
  age_hours: number;
  magic: number;
  is_bot_position: boolean;
  strategy: string | null;
  comment: string;
  r_multiple: number | null;
}

export interface Trade {
  id: number;
  ticket: number | null;
  symbol: string;
  broker_symbol: string | null;
  direction: 'BUY' | 'SELL';
  volume: number;
  entry_price: number | null;
  exit_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  strategy: string | null;
  signal_reason: string | null;
  timeframe: string | null;
  opened_at: string | null;
  closed_at: string | null;
  profit: number;
  commission: number;
  swap: number;
  close_reason: string | null;
  status: 'open' | 'closed' | 'error';
  mode: string;
  risk_percent: number | null;
}

export interface Statistics {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  net_profit: number;
}

export interface Snapshot {
  timestamp: string;
  connection: ConnectionStatus;
  account: AccountInfo | null;
  mode: { demo: boolean; label: string; live_unlocked: boolean; account_is_demo: boolean | null };
  bot: BotStatus;
  risk: RiskSummary | Record<string, never>;
  positions: Position[];
  open_positions: number;
  today_pnl: number;
  statistics: Statistics;
  recent_trades: Trade[];
  server_uptime_seconds: number;
}

export interface LogRecord {
  id?: number;
  timestamp: string;
  category: string;
  message: string;
  symbol: string | null;
  source: string | null;
  details?: unknown;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ChartPayload {
  symbol: string;
  broker_symbol: string;
  timeframe: string;
  digits: number;
  candles: Candle[];
  ema_fast: { time: number; value: number }[];
  ema_slow: { time: number; value: number }[];
  ema_fast_period: number;
  ema_slow_period: number;
  last_price: number | null;
}

export interface MarketRow {
  symbol: string;
  broker_symbol: string | null;
  error: string | null;
  bid?: number;
  ask?: number;
  spread?: number;
  spread_points?: number;
  digits?: number;
  time?: string;
  change_percent?: number;
  day_high?: number;
  day_low?: number;
  tradable?: boolean;
}

export interface BotConfig {
  symbol: string;
  timeframe: string;
  strategy: string;
  traded_symbols: string[];
  symbol_prefix: string;
  symbol_suffix: string;
  magic_number: number;
  deviation_points: number;
  poll_interval_seconds: number;
  risk_per_trade_percent: number;
  max_daily_loss_percent: number;
  max_open_trades: number;
  min_risk_reward: number;
  atr_period: number;
  atr_sl_multiplier: number;
  max_lot_size: number;
  min_lot_size: number | null;
  max_spread_points: number;
  break_even_enabled: boolean;
  break_even_trigger_r: number;
  break_even_offset_points: number;
  trailing_stop_enabled: boolean;
  trailing_start_r: number;
  trailing_distance_atr: number;
  partial_close_enabled: boolean;
  partial_close_at_r: number;
  partial_close_percent: number;
  max_holding_hours: number;
  ema_fast: number;
  ema_slow: number;
  use_rsi_filter: boolean;
  rsi_period: number;
  rsi_buy_max: number;
  rsi_sell_min: number;
  telegram_notifications: boolean;
}

export interface SettingsPayload {
  config: BotConfig;
  environment: Record<string, unknown>;
  timeframes: string[];
  notifications: { configured: boolean; enabled: boolean; messages_sent: number; last_error: string };
  terminal_paths: string[];
}

export interface StrategyParamSpec {
  name: string;
  label: string;
  type: 'int' | 'float' | 'bool' | 'select';
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
  options: string[] | null;
  help: string;
}

export interface StrategyInfo {
  name: string;
  display_name: string;
  description: string;
  params: StrategyParamSpec[];
}

export interface BacktestTradeRow {
  ticket: number;
  direction: 'BUY' | 'SELL';
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  volume: number;
  stop_loss: number;
  take_profit: number;
  profit: number;
  r_multiple: number;
  exit_reason: string;
  balance_after: number;
  bars_held: number;
  reason: string;
}

export interface BacktestResult {
  id?: number;
  symbol: string;
  timeframe: string;
  strategy: string;
  strategy_display_name?: string;
  start_date: string;
  end_date: string;
  initial_balance: number;
  final_balance: number;
  risk_percent: number;
  candles?: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  net_profit: number;
  net_profit_percent: number;
  gross_profit: number;
  gross_loss: number;
  max_drawdown: number;
  max_drawdown_money: number;
  profit_factor: number | null;
  average_win: number;
  average_loss: number;
  largest_win: number;
  largest_loss: number;
  expectancy: number;
  average_bars_held: number;
  equity_curve: { time: string; balance: number; equity: number }[];
  trades: BacktestTradeRow[];
  disclaimer: string;
}

export interface ApiError {
  error?: string;
  message?: string;
  code?: number | null;
}
