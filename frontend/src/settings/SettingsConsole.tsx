import type { ReactElement, ReactNode } from 'react';

export interface SettingsGroupProps {
  title: string;
  description?: string;
  count?: string;
  children: ReactNode;
  className?: string;
}

export function SettingsGroup({
  title,
  description,
  count,
  children,
  className = '',
}: SettingsGroupProps): ReactElement {
  return (
    <section className={`console-group ${className}`.trim()}>
      <div className="console-group-head">
        <div>
          <h3>{title}</h3>
          {description && <p>{description}</p>}
        </div>
        {count && <span className="console-group-count">{count}</span>}
      </div>
      <div className="console-group-body">{children}</div>
    </section>
  );
}

export interface SettingsRowProps {
  label: string;
  description?: string;
  children: ReactNode;
  className?: string;
  settingKey?: string;
}

export function SettingsRow({
  label,
  description,
  children,
  className = '',
  settingKey,
}: SettingsRowProps): ReactElement {
  return (
    <div className={`console-row ${className}`.trim()} data-setting-key={settingKey}>
      <div className="console-row-copy">
        <span className="console-row-label">{label}</span>
        {description && <span className="console-row-description">{description}</span>}
      </div>
      <div className="console-row-control">{children}</div>
    </div>
  );
}

export interface SettingsStatusBadgeProps {
  tone?: 'ok' | 'warn' | 'danger' | 'neutral';
  children: ReactNode;
}

export function SettingsStatusBadge({
  tone = 'neutral',
  children,
}: SettingsStatusBadgeProps): ReactElement {
  return (
    <span className={`console-status-badge ${tone}`}>
      <span className="console-status-dot" aria-hidden />
      {children}
    </span>
  );
}

export interface SettingsMetric {
  label: string;
  value: ReactNode;
  tone?: 'ok' | 'warn' | 'danger' | 'neutral';
}

export function SettingsMetricStrip({ metrics }: { metrics: SettingsMetric[] }): ReactElement {
  return (
    <div className="console-metric-strip" aria-label="设置状态摘要">
      {metrics.map((metric) => (
        <div className={`console-metric ${metric.tone ?? 'neutral'}`} key={metric.label}>
          <span>{metric.label}</span>
          <strong>{metric.value}</strong>
        </div>
      ))}
    </div>
  );
}

export interface SettingsActionBarProps {
  children: ReactNode;
  note?: ReactNode;
  className?: string;
}

export function SettingsActionBar({ children, note, className = '' }: SettingsActionBarProps): ReactElement {
  return (
    <div className={`console-action-bar ${className}`.trim()}>
      <div className="btn-row">{children}</div>
      {note && <span className="console-action-note">{note}</span>}
    </div>
  );
}
