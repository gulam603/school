/** Display helpers - every number in the UI goes through one of these. */

export function money(value: number | null | undefined, currency = '', digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const formatted = value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return currency ? `${currency === 'USD' ? '$' : `${currency} `}${formatted}` : formatted;
}

export function signedMoney(value: number | null | undefined, currency = 'USD'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : value < 0 ? '-' : '';
  return `${sign}${money(Math.abs(value), currency)}`;
}

export function percent(value: number | null | undefined, digits = 2, signed = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const sign = signed && value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

export function price(value: number | null | undefined, digits = 5): string {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) return '--';
  return value.toFixed(digits);
}

export function lots(value: number | null | undefined): string {
  if (value === null || value === undefined) return '--';
  return value.toFixed(2);
}

/** 02:35:42 */
export function duration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return '00:00:00';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  return [hours, minutes, secs].map((part) => String(part).padStart(2, '0')).join(':');
}

export function clockTime(iso?: string | null): string {
  const date = iso ? new Date(iso) : new Date();
  if (Number.isNaN(date.getTime())) return '--:--:--';
  return date.toLocaleTimeString('en-GB', { hour12: false });
}

export function shortDateTime(iso?: string | null): string {
  if (!iso) return '--';
  const date = new Date(iso.replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16);
  return `${date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })} ${date.toLocaleTimeString('en-GB', { hour12: false, hour: '2-digit', minute: '2-digit' })}`;
}

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function daysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return isoDate(date);
}

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return 'neutral';
  return value > 0 ? 'positive' : 'negative';
}

export function profitFactor(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'INF';
  return value.toFixed(2);
}
