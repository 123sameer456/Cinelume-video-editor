import type { CSSProperties, ReactNode } from "react";

export function Section({
  title,
  children,
  actions,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="card">
      <header className="card-head">
        <h2>{title}</h2>
        <div className="card-actions">{actions}</div>
      </header>
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {hint ? <em className="field-hint">{hint}</em> : null}
      </span>
      {children}
    </label>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

export function NumberInput({
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <input
      className="input"
      type="number"
      value={Number.isFinite(value) ? value : 0}
      min={min}
      max={max}
      step={step}
      onChange={(event) => onChange(Number(event.target.value))}
    />
  );
}

export function Select<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <select className="input" value={value} onChange={(event) => onChange(event.target.value as T)}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function ColorInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <input
      className="color"
      type="color"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  const style: CSSProperties = {};
  return (
    <span className="slider">
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        style={style}
      />
      <b>{Math.round(value * 100) / 100}</b>
    </span>
  );
}

export function Button({
  children,
  onClick,
  kind = "default",
  disabled,
  confirm,
}: {
  children: ReactNode;
  onClick: () => void;
  kind?: "default" | "primary" | "danger";
  disabled?: boolean;
  confirm?: string;
}) {
  return (
    <button
      className={`btn btn-${kind}`}
      disabled={disabled}
      onClick={() => {
        if (confirm && !window.confirm(confirm)) {
          return;
        }
        onClick();
      }}
    >
      {children}
    </button>
  );
}

export function Banner({ tone, children }: { tone: "warn" | "error" | "info"; children: ReactNode }) {
  return <div className={`banner banner-${tone}`}>{children}</div>;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "–";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatEta(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "";
  if (seconds < 60) return `${Math.round(seconds)}s left`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s left`;
}
