// A community's written reason, with what a reviewer needs to trust or distrust it (page 05): who wrote it (the
// Anthropic API, a connected agent, or the template), which checks it passed, and the evidence hash it was written
// from. A template says so plainly. A text that failed a check and was replaced says that, with the reason, because a
// reviewer should know the words came from a template and not from a model that was overruled.
import type { Summary } from "../api/types";
import { humanize } from "./score";
import { Notice } from "./ui";

// The three checks on page 05, as the lab's `checks` keys are expected to read. Any other key shows as words.
const CHECKS: Record<string, string> = {
  numbers: "Every number is in the evidence",
  members: "Every account named is a member",
  verdict_words: "No verdict words",
};

const SOURCES: Record<Summary["source"], { badge: string; said: (model?: string | null) => string }> = {
  api: { badge: "api", said: (m) => `Written by the Anthropic API${m ? `, model ${m}` : ""}.` },
  agent: { badge: "agent", said: (m) => `Written by a connected agent through describe_alert${m ? `, model ${m}` : ""}.` },
  template: { badge: "template", said: () => "Built from the evidence by a template. No model wrote this text." },
};

/** Who wrote a summary, as a small badge: "Source: api", "Source: agent" or "Source: template". */
export function SourceBadge({ source }: { source: string }) {
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded-full border border-slate-300 bg-slate-50 px-2.5 py-0.5 text-xs font-semibold text-slate-800">
      Source: {source}
    </span>
  );
}

/** True when a writer's text failed a check and the template took its place. */
export function wasReplaced(s: Summary): boolean {
  return s.source === "template" && (Boolean(s.failed_reason) || Object.values(s.checks).some((ok) => ok === false));
}

export function SummaryBlock({ summary }: { summary: Summary | null | undefined }) {
  if (!summary) {
    return (
      <Notice kind="info" title="No summary for this community">
        Summaries are written for the top communities of a run (20 by default), and only when summaries are on in the recipe.
        The evidence below is all there is to read.
      </Notice>
    );
  }
  const from = SOURCES[summary.source] ?? SOURCES.template;
  const replaced = wasReplaced(summary);
  const failed = Object.entries(summary.checks).filter(([, ok]) => !ok).map(([k]) => CHECKS[k] ?? humanize(k));
  const paragraphs = summary.text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <SourceBadge source={from.badge} />
        <p className="text-sm text-slate-700">{from.said(summary.model)}</p>
      </div>

      {replaced && (
        <Notice kind="warning" title="A model's text was replaced by the template">
          <p>
            The text a model wrote failed a check{failed.length > 0 ? ` (${failed.join("; ")})` : ""}, so this one was built from the evidence instead.
          </p>
          {summary.failed_reason && <p className="mt-1">{summary.failed_reason}</p>}
        </Notice>
      )}
      {!replaced && failed.length > 0 && (
        <Notice kind="error" title="A check failed on this text">
          {failed.join("; ")}. Read it against the evidence before you rely on it.
        </Notice>
      )}

      <div className="space-y-3 text-slate-800">
        {paragraphs.map((p, i) => <p key={i}>{p}</p>)}
      </div>
      <p className="text-sm text-slate-500">
        A summary has three parts: what the evidence shows, why that pattern could mean risk, and the innocent
        explanation to rule out. It describes patterns, not people.
      </p>

      {Object.keys(summary.checks).length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-800">Checks on the written text</h3>
          <ul className="mt-1 space-y-0.5 text-sm">
            {Object.entries(summary.checks).map(([k, ok]) => (
              <li key={k} className={ok ? "text-slate-700" : "font-medium text-red-800"}>
                <span aria-hidden="true">{ok ? "✓" : "✗"} </span>
                {CHECKS[k] ?? humanize(k)}: <span className="font-medium">{ok ? "passed" : "failed"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-slate-500">
        Evidence hash <code title={summary.evidence_hash} className="text-slate-700">{summary.evidence_hash.slice(0, 12)}</code>:
        the same hash with the same model is not written again.
      </p>
    </div>
  );
}
