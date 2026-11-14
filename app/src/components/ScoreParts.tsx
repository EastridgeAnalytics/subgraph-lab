// A score and the parts it is built from, so a reviewer can check the sum (page 04, level three). Each part is a bar
// from 0 to 1 with its value beside it. Give the policy's weights and each row also shows `value x weight`, and the
// foot works the score out again and compares it with the score the lab stored. The policy file can change after a run,
// so when the two disagree the foot says so instead of presenting a result that does not match.
//
// How the parts combine is the policy's `community.combine` (decision 45). With "noisy_or" each row is `value x weight`
// with the weight as it is, and the foot is 1 - the product of (1 - weight x value). With "weighted_mean", or when none is
// given, each weight is a share of the total weight and the foot is the sum of the shares.
//
// `formed_by_identifier` (1 or 0, decision 42) travels in the same object but is not a part: it says how the community
// formed. It is shown as a line, never as a bar, and it is left out of the sum and of the check on the parts.
import { formatScore, humanize } from "./score";

// What each community part measures, from the community policy (lab/contracts/factors.py).
const MEANING: Record<string, string> = {
  member_risk: "The share of members at or above the alert threshold, and their mean score.",
  shared_identifiers: "The share of members that share a device, an email or an address with another member.",
  cycles: "Money that comes back to where it started, each payment after the one before it.",
  new_accounts: "The share of members opened recently.",
  speed: "Money in and out of similar size within a day: the in_and_out_within_a_day factor's rows.",
};

/** The tag the lab keeps beside the parts: how the community formed. Not a part, and it takes no weight. */
export const FORMED = "formed_by_identifier";

const MATCH = 0.01;

export type Combine = "noisy_or" | "weighted_mean";

export function ScoreParts({ parts, weights, combine, score, name = "Score parts" }: {
  /** Part name to value, each 0 to 1. */
  parts: Record<string, number>;
  /** The policy's weight for each part, when known. */
  weights?: Record<string, number> | null;
  /** How the policy combines the parts: `community.combine`. Left out, it is a weighted mean. */
  combine?: Combine | null;
  /** The score the lab stored, to compare with what the parts and weights give. */
  score?: number;
  /** Names the list for screen readers. */
  name?: string;
}) {
  const { [FORMED]: formed, ...scored } = parts;
  const entries = Object.entries(scored);
  const noisy = combine === "noisy_or";
  const weighted = weights ? entries.filter(([k]) => typeof weights[k] === "number") : [];
  const totalWeight = weights ? Object.values(weights).reduce((t, w) => t + w, 0) : 0;
  const sumUsable = weights && weighted.length === entries.length && entries.length > 0 && (noisy || totalWeight > 0);
  // What the parts and the weights give: 1 - the product of (1 - w v) under noisy-or, else the weighted mean.
  const unit = (x: number) => Math.min(1, Math.max(0, x));
  const result = !sumUsable ? 0
    : noisy ? 1 - weighted.reduce((t, [k, v]) => t * (1 - unit(v * weights![k])), 1)
    : weighted.reduce((t, [k, v]) => t + v * weights![k], 0) / totalWeight;
  const agrees = score === undefined || Math.abs(result - score) <= MATCH;

  const tag = formed === undefined ? null : (
    <p className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
      <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-900">
        {formed >= 0.5 ? "Formed by a shared identifier" : "Formed by payments"}
      </span>
      <span>How this community formed. It is not one of the parts and adds nothing to the score.</span>
    </p>
  );
  if (entries.length === 0) {
    return <div className="space-y-3">{tag}<p className="text-sm text-slate-600">The lab gave no parts for this score.</p></div>;
  }
  return (
    <div className="space-y-3">
      {tag}
      <ul aria-label={name} className="space-y-3">
        {entries.map(([key, value]) => {
          const w = weights?.[key];
          // Noisy-or: the weight itself. A weighted mean: the weight's share of the total.
          const times = typeof w !== "number" ? undefined : noisy ? w : totalWeight > 0 ? w / totalWeight : undefined;
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
                {times !== undefined && <span className="text-slate-500"> × {formatScore(times)} = {formatScore(value * times)}</span>}
              </p>
            </li>
          );
        })}
      </ul>
      {sumUsable && (
        <p role={agrees ? undefined : "note"} className={agrees ? "text-sm text-slate-600" : "text-sm font-medium text-amber-900"}>
          {noisy
            ? (agrees
              ? <>The parts combine as 1 − Π(1 − w·v) = {formatScore(result)}, the score the lab stored{score === undefined ? "" : ` (${formatScore(score)})`}.</>
              : <>With the policy file&apos;s weights now, the parts combine as 1 − Π(1 − w·v) = {formatScore(result)}, not the {formatScore(score!)} the lab stored.
                The policy may have changed since this run, so read the parts, not the result.</>)
            : (agrees
              ? <>The parts add up to {formatScore(result)}, the score the lab stored{score === undefined ? "" : ` (${formatScore(score)})`}.</>
              : <>With the policy file&apos;s weights now, the parts add up to {formatScore(result)}, not the {formatScore(score!)} the lab stored.
                The policy may have changed since this run, so read the parts, not the sum.</>)}
        </p>
      )}
      {weights && !sumUsable && (
        <p className="text-sm text-slate-600">The policy has no weight for every part, so the {noisy ? "result" : "sum"} is not shown.</p>
      )}
    </div>
  );
}
