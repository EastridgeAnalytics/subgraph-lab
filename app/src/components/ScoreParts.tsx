// A score and the parts it is built from, so a reviewer can check the sum (page 04, level three). Each part is a bar
// from 0 to 1 with its value beside it. Give the policy's weights and each row also shows `value x weight`, and the
// foot adds them up and compares the total with the score the lab stored. The policy file can change after a run, so
// when the two disagree the foot says so instead of presenting a sum that does not match.
import { formatScore, humanize } from "./score";

// What each community part measures, from the community policy (lab/contracts/factors.py).
const MEANING: Record<string, string> = {
  member_risk: "The share of members at or above the alert threshold, and their mean score.",
  shared_identifiers: "How many identifiers members share, relative to the community's size.",
  cycles: "Money that comes back to where it started inside the community.",
  new_accounts: "The share of members opened recently.",
  speed: "How fast money moves through: in and out within hours.",
};

const MATCH = 0.01;

export function ScoreParts({ parts, weights, score, name = "Score parts" }: {
  /** Part name to value, each 0 to 1. */
  parts: Record<string, number>;
  /** The policy's weight for each part, when known. */
  weights?: Record<string, number> | null;
  /** The score the lab stored, to compare with the weighted sum. */
  score?: number;
  /** Names the list for screen readers. */
  name?: string;
}) {
  const entries = Object.entries(parts);
  const weighted = weights ? entries.filter(([k]) => typeof weights[k] === "number") : [];
  const sum = weighted.reduce((t, [k, v]) => t + v * weights![k], 0);
  const totalWeight = weights ? Object.values(weights).reduce((t, w) => t + w, 0) : 0;
  const sumUsable = weights && weighted.length === entries.length && entries.length > 0 && totalWeight > 0;
  const mean = sumUsable ? sum / totalWeight : 0;
  const agrees = score === undefined || Math.abs(mean - score) <= MATCH;

  if (entries.length === 0) return <p className="text-sm text-slate-600">The lab gave no parts for this score.</p>;
  return (
    <div className="space-y-3">
      <ul aria-label={name} className="space-y-3">
        {entries.map(([key, value]) => {
          const w = weights?.[key];
          const share = typeof w === "number" && totalWeight > 0 ? w / totalWeight : undefined; // the policy takes a weighted mean
          const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
          return (
            <li key={key} className="grid grid-cols-1 gap-1 sm:grid-cols-[14rem_1fr_9rem] sm:items-center sm:gap-4">
              <div>
                <p className="text-sm font-medium text-slate-900">{humanize(key)}</p>
                {MEANING[key] && <p className="text-xs text-slate-500">{MEANING[key]}</p>}
              </div>
              <div role="meter" aria-label={humanize(key)} aria-valuemin={0} aria-valuemax={1} aria-valuenow={value}
                aria-valuetext={formatScore(value)} className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
                <div className="h-full rounded-r bg-indigo-600" style={{ width: `${pct}%` }} />
              </div>
              <p className="text-sm tabular-nums text-slate-700 sm:text-right">
                <span className="font-semibold text-slate-900">{formatScore(value)}</span>
                {share !== undefined && <span className="text-slate-500"> × {formatScore(share)} = {formatScore(value * share)}</span>}
              </p>
            </li>
          );
        })}
      </ul>
      {sumUsable && (
        <p role={agrees ? undefined : "note"} className={agrees ? "text-sm text-slate-600" : "text-sm font-medium text-amber-900"}>
          {agrees
            ? <>The parts add up to {formatScore(mean)}, the score the lab stored{score === undefined ? "" : ` (${formatScore(score)})`}.</>
            : <>With the policy file's weights now, the parts add up to {formatScore(mean)}, not the {formatScore(score!)} the lab stored.
              The policy may have changed since this run, so read the parts, not the sum.</>}
        </p>
      )}
      {weights && !sumUsable && (
        <p className="text-sm text-slate-600">The policy has no weight for every part, so the sum is not shown.</p>
      )}
    </div>
  );
}
