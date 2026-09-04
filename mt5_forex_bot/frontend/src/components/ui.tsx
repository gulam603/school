/** Small presentational building blocks shared by every page. */

import type { CSSProperties, ReactNode } from 'react';

export function Card({
  title, action, children, className = '', bodyClass = '',
}: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title !== undefined && (
        <header className="card-head">
          <span className="card-title">{title}</span>
          <span className="spacer" />
          {action}
        </header>
      )}
      <div className={`card-body ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function Stat({
  label, value, foot, tone = '', style,
}: {
  label: string; value: ReactNode; foot?: ReactNode; tone?: string; style?: CSSProperties;
}) {
  return (
    <div className="stat" style={style}>
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${tone}`}>{value}</span>
      {foot !== undefined && <span className="stat-foot">{foot}</span>}
    </div>
  );
}

export function Pill({ kind = 'muted', children }: { kind?: string; children: ReactNode }) {
  return <span className={`pill ${kind}`}>{children}</span>;
}

export function KeyValue({ label, value, tone = '' }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="kv">
      <span className="kv-key">{label}</span>
      <span className={`kv-val ${tone}`}>{value}</span>
    </div>
  );
}

export function Meter({ value, max, tone = 'accent' }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="meter">
      <div className={`meter-fill ${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Switch({
  checked, onChange, label, disabled = false,
}: {
  checked: boolean; onChange: (value: boolean) => void; label?: ReactNode; disabled?: boolean;
}) {
  return (
    <label className="switch" style={disabled ? { opacity: 0.5, cursor: 'not-allowed' } : undefined}>
      <span className={`switch-track ${checked ? 'on' : ''}`} onClick={() => !disabled && onChange(!checked)}>
        <span className="switch-thumb" />
      </span>
      {label !== undefined && <span>{label}</span>}
    </label>
  );
}

export function Field({
  label, help, children,
}: { label: string; help?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
      {help && <span className="field-help">{help}</span>}
    </div>
  );
}

export function NumberField({
  label, value, onChange, min, max, step = 1, help, suffix, disabled,
}: {
  label: string; value: number; onChange: (value: number) => void;
  min?: number; max?: number; step?: number; help?: string; suffix?: string; disabled?: boolean;
}) {
  return (
    <Field label={suffix ? `${label} (${suffix})` : label} help={help}>
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (!Number.isNaN(next)) onChange(next);
        }}
      />
    </Field>
  );
}

export function SelectField({
  label, value, options, onChange, help, disabled,
}: {
  label: string; value: string; options: { value: string; label: string }[];
  onChange: (value: string) => void; help?: string; disabled?: boolean;
}) {
  return (
    <Field label={label} help={help}>
      <select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    </Field>
  );
}

export function Banner({
  kind = 'info', title, children,
}: { kind?: 'info' | 'warn' | 'bad' | 'ok'; title?: string; children: ReactNode }) {
  return (
    <div className={`banner ${kind}`}>
      <div>
        {title && <strong className="banner-title">{title}</strong>}
        {children}
      </div>
    </div>
  );
}

export function Empty({ icon = '--', children }: { icon?: string; children: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon">{icon}</span>
      {children}
    </div>
  );
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="empty">
      <span className="spinner" /> <span style={{ marginLeft: 8 }}>{label}...</span>
    </div>
  );
}

export function Modal({
  title, children, onClose, footer,
}: { title: string; children: ReactNode; onClose: () => void; footer?: ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">{title}</div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function DirectionTag({ direction }: { direction: string }) {
  const kind = direction === 'BUY' ? 'buy' : direction === 'SELL' ? 'sell' : 'muted';
  return <span className={`pill ${kind}`}>{direction}</span>;
}
