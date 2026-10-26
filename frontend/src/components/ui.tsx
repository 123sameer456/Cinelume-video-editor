import type { CSSProperties, ReactNode } from "react";

export function Section({
  title,
  children,
  actions,
  onHeaderClick,
  open = true,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  onHeaderClick?: () => void;
  open?: boolean;
}) {
  return (
    <section className="card">
      <header
        className="card-head"
        onClick={onHeaderClick}
        style={onHeaderClick ? { cursor: "pointer" } : undefined}
      >
        <h2>
          {onHeaderClick ? (open ? "▾ " : "▸ ") : ""}
          {title}
        </h2>
        <div className="card-actions">{actions}</div>
      </header>
      {open ? <div className="card-body">{children}</div> : null}
    </section>
  );
}

export function Tip({ text }: { text: string }) {
  return (
    <span className="tip" tabIndex={0} role="img" aria-label={text} onClick={(event) => event.preventDefault()}>
      <span className="tip-icon" aria-hidden="true">?</span>
      <span className="tip-bubble" aria-hidden="true">{text}</span>
    </span>
  );
}

export function Field({
  label,
  hint,
  tip,
  children,
}: {
  label: string;
  hint?: string;
  tip?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {tip ? <Tip text={tip} /> : null}
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
  tip,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  tip?: string;
}) {
  return (
    <label className="toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
      {tip ? <Tip text={tip} /> : null}
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
  // Clearing the field must not send 0 (or below min) to the server.
  const safe = Number.isFinite(value) ? value : 0;
  const clamped =
    min !== undefined && safe < min ? min : max !== undefined && safe > max ? max : safe;
  return (
    <input
      className="input"
      type="number"
      value={clamped}
      min={min}
      max={max}
      step={step}
      onChange={(event) => {
        const parsed = Number(event.target.value);
        const next = Number.isFinite(parsed) ? parsed : (min ?? 0);
        onChange(
          min !== undefined && next < min ? min : max !== undefined && next > max ? max : next,
        );
      }}
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
