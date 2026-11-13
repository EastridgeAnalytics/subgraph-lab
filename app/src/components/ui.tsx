// The UI kit every screen uses and nothing else for layout: white background, slate text, one indigo accent.
// Page, Card, Button, Field, Stat, Tabs, Notice, Table. Keep them general; a screen that needs more asks the kit.
import {
  forwardRef, useId, useRef,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type KeyboardEvent, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from "react";

/** Joins class names, dropping the falsy ones. */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";

// ---- Page ---------------------------------------------------------------------------------------------------
/** A screen: its title, one line saying what it is for, and the screen itself. `actions` sits at the top right. */
export function Page({ title, purpose, actions, children }: {
  title: string; purpose: ReactNode; actions?: ReactNode; children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 id={id} className="text-2xl font-semibold text-slate-900">{title}</h1>
          <p className="mt-1 text-slate-600">{purpose}</p>
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}

// ---- Card ---------------------------------------------------------------------------------------------------
/** A bordered block. With a `title` it is a labelled region (screen readers and tests can find it by name). */
export function Card({ title, description, children, className }: {
  title?: string; description?: ReactNode; children: ReactNode; className?: string;
}) {
  const id = useId();
  return (
    <section aria-labelledby={title ? id : undefined}
      className={cx("rounded-lg border border-slate-200 bg-white p-5 shadow-sm", className)}>
      {title && <h2 id={id} className="text-base font-semibold text-slate-900">{title}</h2>}
      {description && <p className="mt-1 text-sm text-slate-600">{description}</p>}
      <div className={cx(title || description ? "mt-4" : undefined, "space-y-4")}>{children}</div>
    </section>
  );
}

// ---- Button -------------------------------------------------------------------------------------------------
function Spinner() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" className="h-4 w-4 animate-spin">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" className="opacity-25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/** A real `<button type="button">`. One primary per screen; `busy` disables it and shows a spinner. Takes a ref. */
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary"; busy?: boolean;
}>(function Button({ variant = "secondary", busy = false, disabled, className, children, type = "button", ...rest }, ref) {
  return (
    <button {...rest} ref={ref} type={type} disabled={disabled || busy} aria-busy={busy || undefined}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
        "disabled:cursor-not-allowed disabled:opacity-50", FOCUS,
        variant === "primary"
          ? "bg-indigo-600 text-white hover:bg-indigo-700 disabled:hover:bg-indigo-600"
          : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:hover:bg-white",
        className)}>
      {busy && <Spinner />}
      {children}
    </button>
  );
});

// ---- Field --------------------------------------------------------------------------------------------------
type FieldBase = { label: string; hint?: ReactNode; error?: string | null };
/** A labelled control. `as` picks input (the default), select (children are the options) or textarea. The rest of the
 *  props go to the control, so `value`, `onChange`, `type`, `placeholder`, `rows` and `min` work as usual. */
export type FieldProps =
  | (FieldBase & { as?: "input" } & InputHTMLAttributes<HTMLInputElement>)
  | (FieldBase & { as: "select" } & SelectHTMLAttributes<HTMLSelectElement>)
  | (FieldBase & { as: "textarea" } & TextareaHTMLAttributes<HTMLTextAreaElement>);

const CONTROL = "block w-full rounded-md border bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 " +
  "disabled:bg-slate-50 disabled:text-slate-500 focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-indigo-600";

export function Field(props: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const { label, hint, error } = props;
  const shared = {
    id,
    "aria-describedby": [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined,
    "aria-invalid": error ? true : undefined,
  } as const;
  const cls = (extra?: string) => cx(CONTROL, error ? "border-red-500" : "border-slate-300", extra);

  let control: ReactNode;
  if (props.as === "select") {
    const { label: _l, hint: _h, error: _e, as: _a, className, ...rest } = props;
    control = <select {...rest} {...shared} className={cls(className)} />;
  } else if (props.as === "textarea") {
    const { label: _l, hint: _h, error: _e, as: _a, className, ...rest } = props;
    control = <textarea {...rest} {...shared} className={cls(className)} />;
  } else {
    const { label: _l, hint: _h, error: _e, as: _a, className, ...rest } = props;
    control = <input {...rest} {...shared} className={cls(className)} />;
  }

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-800">{label}</label>
      {control}
      {hint && <p id={hintId} className="text-sm text-slate-500">{hint}</p>}
      {error && <p id={errorId} className="text-sm text-red-700">{error}</p>}
    </div>
  );
}

// ---- Stat ---------------------------------------------------------------------------------------------------
/** One number or word with its label above it. Put several in a grid. */
export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <dl className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd className="mt-1 text-xl font-semibold text-slate-900">{value}</dd>
      {hint && <dd className="mt-1 text-xs text-slate-500">{hint}</dd>}
    </dl>
  );
}

// ---- Tabs ---------------------------------------------------------------------------------------------------
export interface TabDef { id: string; label: string }

/** Controlled tabs. The caller renders the active panel as `children`. Arrow keys, Home and End move between tabs. */
export function Tabs({ tabs, value, onChange, label, children }: {
  tabs: TabDef[]; value: string; onChange: (id: string) => void; label: string; children: ReactNode;
}) {
  const base = useId();
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = Math.max(0, tabs.findIndex((t) => t.id === value));
    let to = at;
    if (e.key === "ArrowRight") to = (at + 1) % tabs.length;
    else if (e.key === "ArrowLeft") to = (at - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = tabs.length - 1;
    else return;
    e.preventDefault();
    onChange(tabs[to].id);
    buttons.current[tabs[to].id]?.focus();
  };

  return (
    <div>
      <div role="tablist" aria-label={label} onKeyDown={onKeyDown} className="flex gap-1 border-b border-slate-200">
        {tabs.map((t) => {
          const active = t.id === value;
          return (
            <button key={t.id} type="button" role="tab" id={`${base}-tab-${t.id}`} aria-selected={active}
              aria-controls={`${base}-panel`} tabIndex={active ? 0 : -1}
              ref={(el) => { buttons.current[t.id] = el; }} onClick={() => onChange(t.id)}
              className={cx(
                "-mb-px border-b-2 px-4 py-2 text-sm font-medium", FOCUS,
                active ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-600 hover:text-slate-900")}>
              {t.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-tab-${value}`} className="pt-4">{children}</div>
    </div>
  );
}

// ---- Notice -------------------------------------------------------------------------------------------------
const NOTICE: Record<"info" | "success" | "warning" | "error", string> = {
  info: "border-indigo-200 bg-indigo-50 text-indigo-900",
  success: "border-emerald-200 bg-emerald-50 text-emerald-900",
  warning: "border-amber-300 bg-amber-50 text-amber-900",
  error: "border-red-200 bg-red-50 text-red-900",
};

/** A message in a box. Errors and warnings are announced at once (role alert); info and success politely (role status). */
export function Notice({ kind = "info", title, children }: {
  kind?: "info" | "success" | "warning" | "error"; title?: string; children?: ReactNode;
}) {
  return (
    <div role={kind === "error" || kind === "warning" ? "alert" : "status"}
      className={cx("rounded-md border px-4 py-3 text-sm", NOTICE[kind])}>
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={title ? "mt-1" : undefined}>{children}</div>}
    </div>
  );
}

// ---- Table --------------------------------------------------------------------------------------------------
export interface Column<Row> {
  key: string; header: string; render?: (row: Row) => ReactNode; align?: "left" | "right";
}

/** Columns and rows. A column with no `render` shows `row[key]`. No rows shows the `empty` text, never a bare header. */
export function Table<Row extends object>({ columns, rows, empty = "Nothing to show yet.", caption, rowKey }: {
  columns: Column<Row>[]; rows: Row[]; empty?: ReactNode; caption?: string; rowKey?: (row: Row, index: number) => string | number;
}) {
  const cell = (row: Row, c: Column<Row>): ReactNode =>
    c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "");
  const align = (c: Column<Row>) => (c.align === "right" ? "text-right" : "text-left");
  return (
    <div className="overflow-x-auto rounded-md border border-slate-200">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="bg-slate-50">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={cx("px-4 py-2 font-medium text-slate-700", align(c))}>{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {rows.length === 0 ? (
            <tr><td colSpan={columns.length} className="px-4 py-6 text-center text-slate-500">{empty}</td></tr>
          ) : rows.map((row, i) => (
            <tr key={rowKey ? rowKey(row, i) : i}>
              {columns.map((c) => <td key={c.key} className={cx("px-4 py-2 text-slate-800", align(c))}>{cell(row, c)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
