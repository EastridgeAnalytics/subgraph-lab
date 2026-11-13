// Review and push (pages 01 and 06): the dry-run plan and the lab's delta, then one confirm. This is the only screen in
// the app that writes to the master graph, and it writes only through `api.push(id, true)`, which is called from the
// confirm dialog and nowhere else: not from the button that opens it, not from Resume push. It says first, in one
// sentence, what the push will do; it never offers a delete, because a push never deletes.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ConnectResult, Delta, PushEntity, PushResult, Review as ReviewData } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { errorMessage, formatInt, formatMB } from "../components/format";
import { NoRunNotice, useRunRef } from "../components/RunRef";
import { Button, Card, Notice, Page, Stat, Table, type Column } from "../components/ui";

const plural = (n: number, one: string, many = `${one}s`) => `${formatInt(n)} ${n === 1 ? one : many}`;

/** The one sentence the screen opens with. Every number is the delta's. */
export function summarySentence(d: Delta): string {
  return `This push adds ${plural(d.new_alerts, "alert")} and ${plural(d.new_communities, "community", "communities")}, ` +
    `updates scores on ${plural(d.accounts_scored, "account")}, and deletes nothing.`;
}

const entityName = (e: PushEntity) => `${e.entity} (${e.kind})`;
const linkClass = "font-medium text-indigo-700 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";

// Plan rows grouped by kind: nodes, then relationships, then any other kind the lab reports.
const KIND_TITLES: Record<string, string> = { node: "Nodes", relationship: "Relationships" };
function groupByKind(plan: PushEntity[]): { kind: string; title: string; rows: PushEntity[] }[] {
  const kinds = [...new Set(plan.map((e) => e.kind))].sort((a, b) => {
    const rank = (k: string) => (k === "node" ? 0 : k === "relationship" ? 1 : 2);
    return rank(a) - rank(b);
  });
  return kinds.map((kind) => ({
    kind, title: KIND_TITLES[kind] ?? `Other: ${kind}`, rows: plan.filter((e) => e.kind === kind),
  }));
}

const PLAN_COLUMNS: Column<PushEntity>[] = [
  { key: "entity", header: "Entity" },
  { key: "total", header: "Total", align: "right", render: (e) => formatInt(e.total) },
  { key: "created", header: "Created", align: "right", render: (e) => formatInt(e.created) },
  { key: "updated", header: "Updated", align: "right", render: (e) => formatInt(e.updated) },
  { key: "skipped", header: "Skipped", align: "right",
    render: (e) => (e.skipped > 0 ? <span className="font-medium text-amber-800">{formatInt(e.skipped)}</span> : formatInt(e.skipped)) },
  { key: "batches", header: "Batches", align: "right", render: (e) => formatInt(e.batches) },
];

const RESULT_COLUMNS: Column<PushEntity>[] = [
  { key: "entity", header: "Entity", render: entityName },
  { key: "created", header: "Created", align: "right", render: (e) => formatInt(e.created) },
  { key: "updated", header: "Updated", align: "right", render: (e) => formatInt(e.updated) },
  { key: "landed", header: "Landed", render: (e) => (e.landed ? "Yes" : <span className="font-medium text-red-800">No</span>) },
  { key: "error", header: "Error", render: (e) => e.error ?? "" },
];

// Score changes: buckets like "0.0-0.1", in order of their lower bound.
const bucketStart = (b: string) => { const n = parseFloat(b); return Number.isNaN(n) ? Infinity : n; };
function ScoreChanges({ changes }: { changes: Record<string, number> }) {
  const rows = Object.entries(changes)
    .sort(([a], [b]) => bucketStart(a) - bucketStart(b) || (a < b ? -1 : a > b ? 1 : 0))
    .map(([bucket, accounts]) => ({ bucket, accounts }));
  const max = Math.max(1, ...rows.map((r) => r.accounts));
  const columns: Column<(typeof rows)[number]>[] = [
    { key: "bucket", header: "Score moved by" },
    { key: "accounts", header: "Accounts",
      render: (r) => (
        <div className="flex items-center gap-3">
          {/* One series from one baseline; the count sits at the tip, so no legend and no axis are needed. */}
          <div className="h-3 min-w-32 flex-1" aria-hidden="true">
            <div className="h-3 rounded-r bg-indigo-600" style={{ width: `${r.accounts > 0 ? Math.max(1, (r.accounts / max) * 100) : 0}%` }} />
          </div>
          <span className="w-20 shrink-0 text-right tabular-nums">{formatInt(r.accounts)}</span>
        </div>
      ) },
  ];
  return <Table columns={columns} rows={rows} rowKey={(r) => r.bucket} caption="Accounts by how far their score moved"
    empty="No account scores moved." />;
}

/** Which master graph the push goes to, said in words, or why the lab could not say. */
function targetText(t: { result: ConnectResult } | { problem: string } | null): string | null {
  if (!t) return null;
  if ("problem" in t) return `The lab could not say which graph is connected: ${t.problem}`;
  if (!t.result.ok) return `The lab could not confirm the connection: ${t.result.message}`;
  return t.result.query_uri ? `Master graph: ${t.result.query_uri}` : "Master graph: the one the lab is connected to";
}

type Asking = "push" | "resume" | null;

export default function Review() {
  const { runId, invalid, withRun } = useRunRef();

  // The review, the push outcome and every error each carry the run they belong to, so a new run clears them.
  const [loaded, setLoaded] = useState<{ id: string; review: ReviewData } | null>(null);
  const [problem, setProblem] = useState<{ id: string; text: string } | null>(null);
  const [round, setRound] = useState(0);
  const [connection, setConnection] = useState<{ result: ConnectResult } | { problem: string } | null>(null);
  const [asking, setAsking] = useState<Asking>(null);
  const [pushing, setPushing] = useState(false);
  const inFlight = useRef(false); // set at once, so a second click in the same instant cannot send a second push
  const [outcome, setOutcome] = useState<{ id: string; result: PushResult } | null>(null);
  const [pushProblem, setPushProblem] = useState<{ id: string; text: string } | null>(null);

  useEffect(() => {
    if (!runId) return;
    let live = true;
    setProblem(null);
    api.review(runId)
      .then((review) => { if (live) setLoaded({ id: runId, review }); })
      .catch((e) => { if (live) setProblem({ id: runId, text: errorMessage(e) }); });
    return () => { live = false; };
  }, [runId, round]);

  // Which graph this writes to. Asked once a run is on screen; the screen works without it and says so when it cannot tell.
  const hasRun = runId !== null;
  useEffect(() => {
    if (!hasRun) return;
    let live = true;
    api.connection()
      .then((result) => { if (live) setConnection({ result }); })
      .catch((e) => { if (live) setConnection({ problem: errorMessage(e) }); });
    return () => { live = false; };
  }, [hasRun]);

  const confirmed = async () => {
    if (!runId || inFlight.current) return;
    inFlight.current = true; setPushing(true); setAsking(null); setPushProblem(null);
    try {
      const result = await api.push(runId, true); // the only place the app writes to the master graph
      setOutcome({ id: runId, result });
    } catch (e) {
      setPushProblem({ id: runId, text: errorMessage(e) });
    } finally {
      inFlight.current = false; setPushing(false);
    }
  };

  const purpose = "See exactly what will be written to the master graph, then confirm once. Nothing is ever deleted.";
  if (!runId) return <Page title="Review and push" purpose={purpose}><NoRunNotice invalid={invalid} /></Page>;

  const review = loaded && loaded.id === runId ? loaded.review : null;
  const error = problem && problem.id === runId ? problem.text : null;
  const result = outcome && outcome.id === runId ? outcome.result : null;
  const failedPush = pushProblem && pushProblem.id === runId ? pushProblem.text : null;
  const target = targetText(connection);

  if (!review) {
    return (
      <Page title="Review and push" purpose={purpose}>
        {error ? (
          <Notice kind="error" title="The review did not load">
            <p>{error}</p>
            <div className="mt-3"><Button onClick={() => setRound((r) => r + 1)}>Try again</Button></div>
          </Notice>
        ) : <p role="status" className="text-sm text-slate-600">Loading the review for run {runId}…</p>}
      </Page>
    );
  }

  const { delta, plan } = review;
  const sentence = summarySentence(delta);
  const groups = groupByKind(plan);
  const pushed = result?.pushed === true;
  const partial = result !== null && !result.pushed;
  const notLanded = partial ? result.entities.filter((e) => !e.landed) : [];
  const created = result ? result.entities.reduce((n, e) => n + e.created, 0) : 0;
  const updated = result ? result.entities.reduce((n, e) => n + e.updated, 0) : 0;

  return (
    <Page title="Review and push" purpose={purpose}>
      <section aria-label="What this push will do" className="space-y-2 rounded-lg border-2 border-indigo-600 bg-white p-5">
        <p className="text-lg font-semibold text-slate-900">{sentence}</p>
        <p className="text-sm text-slate-600">Run <code className="text-slate-900">{review.run_id}</code></p>
        {target && <p className="text-sm text-slate-600">{target}</p>}
      </section>

      {delta.missing_remote_keys > 0 && (
        <Notice kind="warning" title="Some accounts are not on the master graph">
          {formatInt(delta.missing_remote_keys)} of the {formatInt(delta.sampled)} accounts the lab sampled were not found there.
          The push merges on keys, and a merge creates a node when its key is not found, so this push may create those
          accounts with only their key and risk scores.
        </Notice>
      )}

      <Card title="What changes" description="The lab's own comparison with the master graph, from a sample of its keys.">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Stat label="New alerts" value={formatInt(delta.new_alerts)} />
          <Stat label="New communities" value={formatInt(delta.new_communities)} />
          <Stat label="Accounts scored" value={formatInt(delta.accounts_scored)} />
          <Stat label="Vectors" value={formatInt(delta.vectors)} hint="Embeddings sent with the accounts" />
          <Stat label="Dispositions" value={formatInt(delta.dispositions)} hint="Reviewer decisions, with who and when" />
          <Stat label="Data to send" value={formatMB(delta.bytes_estimate)} hint="An estimate" />
        </div>
        {delta.missing_remote_keys === 0 && (
          <p className="text-sm text-slate-600">
            {delta.sampled > 0
              ? `All ${formatInt(delta.sampled)} sampled accounts were found on the master graph.`
              : "The lab did not sample the master graph for missing keys."}
          </p>
        )}
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-slate-800">Score changes, by how far a score moved</h3>
          <ScoreChanges changes={delta.score_changes} />
        </div>
      </Card>

      <Card title="The plan" description={
        "What relate push --dry-run would send. Created is the plan, not a comparison with the master graph, " +
        "so some of those may already be there. The delta above is the comparison."}>
        {groups.length === 0 && <Table columns={PLAN_COLUMNS} rows={[]} caption="The plan" empty="The dry run lists nothing to send." />}
        {groups.map((g) => (
          <div key={g.kind} className="space-y-2">
            <h3 className="text-sm font-medium text-slate-800">{g.title}</h3>
            <Table columns={PLAN_COLUMNS} rows={g.rows} rowKey={(e) => e.entity} caption={`The plan: ${g.title.toLowerCase()}`} />
          </div>
        ))}
        {plan.some((e) => e.skipped > 0) && (
          <p className="text-sm text-slate-600">Skipped is what the push leaves out, for example a label with no key. Nothing is guessed.</p>
        )}
        <details className="rounded-md border border-slate-200">
          <summary className="cursor-pointer px-4 py-2 text-sm font-medium text-slate-800 focus-visible:outline-2 focus-visible:outline-indigo-600">
            The dry run, as Relate printed it
          </summary>
          <pre tabIndex={0} aria-label="Output of relate push --dry-run"
            className="max-h-96 overflow-auto border-t border-slate-200 bg-slate-50 p-4 font-mono text-sm leading-relaxed text-slate-800">
            {review.plan_text || "The dry run printed nothing."}
          </pre>
        </details>
      </Card>

      <Card title="Push to the master graph"
        description="Nothing is sent until you confirm in the next step. A push never deletes anything.">
        {!result && (
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" busy={pushing} onClick={() => setAsking("push")}>Push to the master graph</Button>
            {pushing && <span role="status" className="text-sm text-slate-600">Sending to the master graph. This can take a while for a large run.</span>}
          </div>
        )}
        {failedPush && (
          <Notice kind="error" title="The push request failed">
            <p>{failedPush}</p>
            <p className="mt-1">Nothing is deleted on the master graph. The lab keeps a manifest of what landed, so pushing again sends only the rest.</p>
          </Notice>
        )}

        {pushed && result && (
          <>
            <Notice kind="success" title={result.resumed ? "Pushed. The push resumed and finished." : "Pushed to the master graph"}>
              <p>{result.message}</p>
              <p className="mt-1">
                {plural(result.entities.length, "entity", "entities")} landed: {formatInt(created)} created, {formatInt(updated)} updated.
                Nothing was deleted.
              </p>
              <p className="mt-2"><Link to={withRun("/results")} className={linkClass}>Go to Results</Link></p>
            </Notice>
            <Table columns={RESULT_COLUMNS} rows={result.entities} rowKey={entityName} caption="What landed" />
          </>
        )}

        {partial && result && (
          <>
            <Notice kind="error" title="The push stopped part way. Nothing was deleted.">
              <p>{result.message}</p>
              <p className="mt-2 font-medium">Did not land:</p>
              {notLanded.length > 0 ? (
                <ul className="list-disc pl-5">
                  {notLanded.map((e) => <li key={entityName(e)}>{entityName(e)}: {e.error || "the lab gave no reason"}</li>)}
                </ul>
              ) : <p>The lab did not say which entities failed.</p>}
              <p className="mt-2">The rest landed. A resume sends what did not, and a batch sent twice is harmless because the merge is keyed.</p>
            </Notice>
            <Table columns={RESULT_COLUMNS} rows={result.entities} rowKey={entityName} caption="What landed so far" />
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" busy={pushing} onClick={() => setAsking("resume")}>Resume push</Button>
              {pushing && <span role="status" className="text-sm text-slate-600">Sending the rest to the master graph.</span>}
            </div>
          </>
        )}
      </Card>

      <ConfirmDialog open={asking !== null} onCancel={() => setAsking(null)} onConfirm={confirmed}
        title={asking === "resume" ? "Resume the push?" : "Push to the master graph?"}
        confirmLabel={asking === "resume" ? "Confirm and resume push" : "Confirm and push"}>
        {target && <p className="font-medium text-slate-900">{target}</p>}
        {asking === "resume" ? (
          <>
            <p>The push stopped part way. Resuming sends what did not land:</p>
            <ul className="list-disc pl-5">
              {(notLanded.length > 0 ? notLanded : [null]).map((e) => (
                <li key={e ? entityName(e) : "unknown"}>{e ? entityName(e) : "the lab did not name them"}</li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <p className="font-medium text-slate-900">{sentence}</p>
            <p>
              Also written: {plural(delta.vectors, "vector")} and {plural(delta.dispositions, "reviewer decision")}, about {formatMB(delta.bytes_estimate)} in
              all.
            </p>
          </>
        )}
        <ul className="list-disc space-y-1 pl-5">
          <li><span className="font-medium text-slate-900">Nothing is deleted</span> on the master graph. A push never deletes.</li>
          <li>Everything carries run id <code>{review.run_id}</code>. Pushing the same run twice changes nothing the second time.</li>
          <li>
            Before the first push the lab creates uniqueness constraints on its own labels only (Alert, RiskCommunity, LabRun),
            if they are not there yet. Never on one of your labels.
          </li>
          <li>A LabRun node is written last. If it is there, the run landed whole.</li>
        </ul>
      </ConfirmDialog>
    </Page>
  );
}
