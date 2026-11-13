// Run (page 06): one bar per step with live counts. The screen follows one run (the run context's, or `?run=` in the
// address), asks the lab for its manifest about once a second while the run is going, and stops when it is done or
// failed. A failed step says what failed and offers Resume. A fallback is always shown, with its step, route and reason.
// This screen never writes to the master graph: a push that stopped is resumed on Review and push, not here.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Fallback, Route, RunManifest, Step, StepStatus } from "../api/types";
import { errorMessage, formatInt } from "../components/format";
import { ProgressBar, type ProgressTone } from "../components/Progress";
import { NoRunNotice, useRunRef } from "../components/RunRef";
import { Button, Card, cx, Notice, Page } from "../components/ui";

// The order the lab runs its steps in (STEP_ORDER in lab/contracts/manifest.py). The manifest lists them in this
// order too, but a step missing from it still gets its row, so the screen never shows fewer than the lab runs.
export const STEP_ORDER = [
  "pull", "indexes", "first_pass", "slice", "models", "score", "communities", "summaries", "stage", "review", "push",
] as const;

export const STEP_TITLES: Record<string, string> = {
  pull: "Pull the slice from the master graph",
  indexes: "Build the search indexes",
  first_pass: "First pass: embeddings and summaries",
  slice: "Pick the slice",
  models: "Run the models",
  score: "Score the accounts",
  communities: "Find the communities",
  summaries: "Write the community summaries",
  stage: "Stage the results in the push store",
  review: "Prepare the review: dry run and delta",
  push: "Push to the master graph",
};

const POLL_MS = 1000;
const IN_PROGRESS: readonly StepStatus[] = ["pending", "running"];
const ROUTE_NAMES: Record<Route, string> = { relate: "Relate", bolt: "Bolt" };

const humanize = (key: string) => {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};
export const stepTitle = (name: string) => STEP_TITLES[name] ?? humanize(name);

/** "0.4 s", "12 s", "2 min 05 s", "1 h 03 min". Under a tenth of a second it says so rather than "0.0 s". */
export function formatDuration(ms: number): string {
  if (ms < 100) return "under 0.1 s";
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

/** How long a step took, or has taken so far while it runs. Null when the manifest has no start for it. */
function elapsedMs(step: Step, now: number): number | null {
  if (!step.started_at) return null;
  const start = Date.parse(step.started_at);
  const end = step.finished_at ? Date.parse(step.finished_at) : step.status === "running" ? now : NaN;
  return Number.isNaN(start) || Number.isNaN(end) ? null : Math.max(0, end - start);
}

const STATUS_LABEL: Record<StepStatus, string> = {
  pending: "Waiting", running: "Running", done: "Done", failed: "Failed", skipped: "Skipped",
};
const DOT: Record<StepStatus, string> = {
  pending: "bg-slate-300", running: "bg-indigo-600", done: "bg-emerald-600", failed: "bg-red-600", skipped: "bg-slate-400",
};
const BAR: Record<StepStatus, { value: number | null; tone: ProgressTone }> = {
  pending: { value: 0, tone: "slate" }, running: { value: null, tone: "indigo" }, done: { value: 1, tone: "green" },
  failed: { value: 1, tone: "red" }, skipped: { value: 0, tone: "slate" },
};

/** The steps to show: every step in STEP_ORDER (a pending one when the manifest has none), then any extra the lab added. */
function stepsOf(m: RunManifest): Step[] {
  const known: Step[] = STEP_ORDER.map((name) => m.steps.find((s) => s.name === name) ?? { name, status: "pending", counts: {} });
  const extra = m.steps.filter((s) => !(STEP_ORDER as readonly string[]).includes(s.name));
  return [...known, ...extra];
}

function StepRow({ step, run, fallback, now, resume }: {
  step: Step; run: RunManifest; fallback: Fallback | undefined; now: number; resume: ReactNode;
}) {
  const finished = run.status === "done" || run.status === "failed";
  const label = step.status === "pending" && finished ? "Not run" : STATUS_LABEL[step.status];
  const title = stepTitle(step.name);
  const bar = BAR[step.status];
  const took = elapsedMs(step, now);
  const counts = Object.entries(step.counts);
  const route = run.routes[step.name];
  const failed = step.status === "failed";
  return (
    <li className="space-y-2 py-4">
      <div className="grid grid-cols-1 items-center gap-x-4 gap-y-2 md:grid-cols-[minmax(0,20rem)_minmax(0,1fr)_minmax(0,10rem)]">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className={cx("h-2.5 w-2.5 shrink-0 rounded-full", DOT[step.status])} />
          <span className="font-medium text-slate-900">{title}</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex-1"><ProgressBar label={title} value={bar.value} tone={bar.tone} valueText={label} /></div>
          <span className={cx("w-16 shrink-0 text-sm", failed ? "font-medium text-red-800" : "text-slate-600")}>{label}</span>
        </div>
        <div className="text-sm text-slate-600 md:text-right">{took === null ? "" : formatDuration(took)}</div>
      </div>

      {(counts.length > 0 || route) && (
        <p className="flex flex-wrap gap-x-4 gap-y-1 pl-[1.125rem] text-sm text-slate-600">
          {counts.map(([k, v]) => (
            <span key={k}>{humanize(k)} <span className="font-medium text-slate-900">{formatInt(v)}</span></span>
          ))}
          {route && <span>Route <span className="font-medium text-slate-900">{ROUTE_NAMES[route]}{fallback?.route === route ? " (fallback)" : ""}</span></span>}
        </p>
      )}
      {step.status === "pending" && step.name === "push" && (
        <p className="pl-[1.125rem] text-sm text-slate-600">Nothing is sent until you confirm on Review and push.</p>
      )}
      {!failed && step.note && <p className="pl-[1.125rem] text-sm text-slate-600">{step.note}</p>}
      {failed && (
        <div className="pl-[1.125rem]">
          <Notice kind="error" title={`${title} failed`}>
            <p>{step.note || "The lab did not say why."}</p>
            {resume && <div className="mt-3">{resume}</div>}
          </Notice>
        </div>
      )}
    </li>
  );
}

export default function Run() {
  const navigate = useNavigate();
  const { runId, invalid, withRun } = useRunRef();

  // What the lab last said, and the last error, each with the run it is about: switch runs and the old ones go away.
  const [seen, setSeen] = useState<{ id: string; manifest: RunManifest } | null>(null);
  const [problem, setProblem] = useState<{ id: string; text: string } | null>(null);
  const [round, setRound] = useState(0); // bumped to start polling again (after Resume, after an error)
  const [resuming, setResuming] = useState(false);
  const resumeBusy = useRef(false);
  const [resumeProblem, setResumeProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const manifest = await api.run(runId);
        if (!live) return;
        setSeen({ id: runId, manifest });
        setProblem(null);
        if (IN_PROGRESS.includes(manifest.status)) timer = setTimeout(poll, POLL_MS);
      } catch (e) {
        if (live) setProblem({ id: runId, text: errorMessage(e) }); // polling stops; "Try again" starts it
      }
    };
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [runId, round]);

  const onResume = async () => {
    if (!runId || resumeBusy.current) return;
    resumeBusy.current = true; setResuming(true); setResumeProblem(null);
    try {
      await api.resume(runId);
      setRound((r) => r + 1);
    } catch (e) {
      setResumeProblem(errorMessage(e));
    } finally {
      resumeBusy.current = false; setResuming(false);
    }
  };

  const purpose = "One bar per step, with live counts. A step that fails says what failed and offers to resume.";
  if (!runId) return <Page title="Run" purpose={purpose}><NoRunNotice invalid={invalid} /></Page>;

  const manifest = seen && seen.id === runId ? seen.manifest : null;
  const error = problem && problem.id === runId ? problem.text : null;
  const steps = manifest ? stepsOf(manifest) : [];
  const doneCount = steps.filter((s) => s.status === "done").length;
  const now = Date.now();

  // A push that stopped is resumed where it was confirmed. Resume here is for the steps before it.
  const resumeFor = (step: Step) => step.name === "push"
    ? <Button onClick={() => navigate(withRun("/review"))}>Open Review and push</Button>
    : <Button variant="primary" busy={resuming} onClick={onResume}>Resume</Button>;
  const failedStep = steps.find((s) => s.status === "failed");

  return (
    <Page title="Run" purpose={purpose}>
      {error && (
        <Notice kind="error" title="The lab did not answer for this run">
          <p>{error}</p>
          <div className="mt-3"><Button onClick={() => setRound((r) => r + 1)}>Try again</Button></div>
        </Notice>
      )}
      {!manifest && !error && <p role="status" className="text-sm text-slate-600">Loading run {runId}…</p>}

      {manifest && (
        <>
          <p role="status" className="text-sm text-slate-600">
            Run <code className="text-slate-900">{manifest.run_id}</code>{" · "}
            {formatInt(doneCount)} of {formatInt(steps.length)} steps done{" · "}
            <span className="font-medium text-slate-900">{STATUS_LABEL[manifest.status]}</span>
          </p>

          {manifest.fallbacks.map((f, i) => (
            <Notice key={`${f.step}-${f.at}-${i}`} kind="warning" title={`${stepTitle(f.step)}: took the ${ROUTE_NAMES[f.route]} fallback route`}>
              {f.reason}
            </Notice>
          ))}

          {manifest.status === "failed" && !failedStep && (
            <Notice kind="error" title="The run failed">
              <p>The lab did not name the step that failed.</p>
              <div className="mt-3"><Button variant="primary" busy={resuming} onClick={onResume}>Resume</Button></div>
            </Notice>
          )}
          {resumeProblem && <Notice kind="error" title="The run did not resume">{resumeProblem}</Notice>}

          <Card title="Steps">
            <ol aria-label="Steps" className="-my-4 divide-y divide-slate-100">
              {steps.map((s) => (
                <StepRow key={s.name} step={s} run={manifest} now={now}
                  fallback={manifest.fallbacks.find((f) => f.step === s.name)}
                  resume={s === failedStep ? resumeFor(s) : null} />
              ))}
            </ol>
          </Card>

          {manifest.status === "done" && (
            <div className="flex flex-wrap gap-3">
              <Button variant="primary" onClick={() => navigate(withRun("/results"))}>See the results</Button>
              <Button onClick={() => navigate(withRun("/review"))}>Review and push</Button>
            </div>
          )}
        </>
      )}
    </Page>
  );
}
