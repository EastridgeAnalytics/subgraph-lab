// A thin progress bar: one series, one baseline, a 4px rounded end. `value` is 0 to 1; `null` means "working, no
// fraction known", drawn as a short bar that pulses (and sits still when the reader asks for less motion).
import { cx } from "./ui";

export type ProgressTone = "indigo" | "green" | "red" | "slate";

const FILL: Record<ProgressTone, string> = {
  indigo: "bg-indigo-600", green: "bg-emerald-600", red: "bg-red-600", slate: "bg-slate-400",
};

/** `label` names what is progressing (read by screen readers); `valueText` says where it stands in words. */
export function ProgressBar({ label, value, tone = "indigo", valueText }: {
  label: string; value: number | null; tone?: ProgressTone; valueText?: string;
}) {
  const pct = value === null ? null : Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={pct ?? undefined} aria-valuetext={valueText}
      className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
      <div className={cx("h-full rounded-r", FILL[tone], pct === null && "w-1/3 motion-safe:animate-pulse")}
        style={pct === null ? undefined : { width: `${pct}%` }} />
    </div>
  );
}
