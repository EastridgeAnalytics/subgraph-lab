// The reviewer's decision on one account or community: confirmed, false positive or needs more (page 04). Three buttons
// in a group; the one that is the current decision is pressed (and marked with a check, so it does not rely on colour).
// Under them, in words, what was decided, by whom when it is known, and that it is staged: it travels with the next push.
//
// The control does not call the lab. `onChoose` does, and the screen passes back `value`, `saving` and `error`.
import type { Disposition } from "../api/types";
import { cx } from "./ui";

export const DECISIONS: { id: Disposition; label: string }[] = [
  { id: "confirmed", label: "Confirmed" },
  { id: "false_positive", label: "False positive" },
  { id: "needs_more", label: "Needs more" },
];

export function decisionLabel(d: Disposition): string {
  return DECISIONS.find((x) => x.id === d)?.label ?? d;
}

export function DispositionButtons({ subject, value, by, staged = true, saving = false, error, disabledReason, onChoose }: {
  /** What is being decided, for screen readers: "A0011" or "community 3fa91c07". */
  subject: string;
  /** The decision on record, if any. */
  value?: Disposition | null;
  /** Who made it, when this screen knows. A decision loaded from the lab does not say. */
  by?: string | null;
  /** False when the lab took the decision but did not stage it for the next push. */
  staged?: boolean;
  saving?: boolean;
  /** Why the last attempt did not save. */
  error?: string | null;
  /** When set the buttons are off, and this says why. */
  disabledReason?: string | null;
  onChoose: (d: Disposition) => void;
}) {
  const off = saving || Boolean(disabledReason);
  return (
    <div className="space-y-1.5">
      <div role="group" aria-label={`Decision for ${subject}`} className="flex flex-wrap gap-1.5">
        {DECISIONS.map((d) => {
          const pressed = value === d.id;
          return (
            <button key={d.id} type="button" disabled={off} aria-pressed={pressed}
              aria-label={`Mark ${subject} as ${d.label.toLowerCase()}`} title={disabledReason ?? undefined}
              onClick={() => onChoose(d.id)}
              className={cx(
                "whitespace-nowrap rounded-md border px-2.5 py-1 text-xs font-medium",
                "disabled:cursor-not-allowed disabled:opacity-50",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600",
                pressed
                  ? "border-indigo-600 bg-indigo-600 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:hover:bg-white")}>
              {pressed ? "✓ " : ""}{d.label}
            </button>
          );
        })}
      </div>
      {saving && <p role="status" className="text-xs text-slate-600">Saving…</p>}
      {error && <p role="alert" className="text-xs font-medium text-red-800">Not saved: {error}</p>}
      {!saving && value && (
        <p className="text-xs text-slate-600">
          <span className="font-medium text-slate-800">{decisionLabel(value)}{by ? ` by ${by}` : ""}.</span>{" "}
          {staged ? "Staged: it will travel with the next push." : "Recorded, but not staged: it will not travel with the next push."}
        </p>
      )}
    </div>
  );
}
