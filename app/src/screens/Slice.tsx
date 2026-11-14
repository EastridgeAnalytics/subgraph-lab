// Slice (pages 02 and 06): choose the part of the graph the models work on, in one of three ways, and see its size
// and memory before anything runs. The one decision: run this slice. Estimate comes first, and Run stays off until
// there is an estimate for what is on screen now, and off again when that estimate is over the cap.
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { PullSpec, Recipe, SearchSpec, SliceEstimate } from "../api/types";
import { errorMessage, formatInt, formatMB } from "../components/format";
import { Button, Card, Field, Notice, Page, Stat, Table, Tabs, type Column } from "../components/ui";
import { stableJson, useRecipe, useRunId } from "../state/run";

type Mode = "schema" | "cypher" | "search";
const TABS = [
  { id: "schema", label: "By schema" },
  { id: "cypher", label: "By Cypher" },
  { id: "search", label: "By search" },
];

// The documented defaults for a search slice (page 02; LAB_SEARCH_K and LAB_SEARCH_HOPS on page 07), shown as the
// starting values. Clearing a field sends nothing, and then the lab uses its own setting. A hop is one account-to-account
// step, by a payment or by a shared identifier (decision 41), so the default is 1.
const DEFAULT_K = 50;
const DEFAULT_HOPS = 1;

// An example that runs on the bundled seed: October's payments with both accounts.
const EXAMPLE_QUERY = `MATCH (a:Account)-[t:TX]->(b:Account)
WHERE t.ts >= datetime('2026-10-01T00:00:00Z')
RETURN a, t, b`;

interface Fields {
  labels: string; types: string; since: string; until: string; // by schema
  query: string; // by Cypher
  text: string; account: string; k: string; hops: string; // by search
}

function fieldsFrom(r: Recipe): Fields {
  const s = r.search;
  return {
    labels: (r.pull.labels ?? []).join(", "), types: (r.pull.types ?? []).join(", "),
    since: r.pull.since ?? "", until: r.pull.until ?? "",
    query: r.pull.query || EXAMPLE_QUERY,
    text: s?.text ?? "", account: s?.account ?? "",
    k: s ? (s.k == null ? "" : String(s.k)) : String(DEFAULT_K),
    hops: s ? (s.hops == null ? "" : String(s.hops)) : String(DEFAULT_HOPS),
  };
}

const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

function count(s: string, min: number): { value: number | null; error: string | null } {
  if (s.trim() === "") return { value: null, error: null };
  const n = Number(s);
  return Number.isInteger(n) && n >= min
    ? { value: n, error: null }
    : { value: null, error: `Use a whole number, ${min} or more.` };
}

// The recipe this screen is showing: the context's recipe with its pull and search replaced by the fields.
// A search slice runs inside a broad slice, so in that mode the pull is the by-schema one.
function draftOf(base: Recipe, mode: Mode, f: Fields): Recipe {
  const params = base.pull.params && Object.keys(base.pull.params).length ? { params: base.pull.params } : {};
  const pull: PullSpec = mode === "cypher"
    ? { mode: "cypher", query: f.query.trim(), ...params }
    : { mode: "schema", labels: split(f.labels), types: split(f.types), since: f.since || null, until: f.until || null };
  const search: SearchSpec | null = mode === "search"
    ? { text: f.text.trim() || null, account: f.account.trim() || null, k: count(f.k, 1).value, hops: count(f.hops, 0).value }
    : null;
  return { ...base, pull, search };
}

const FOUND_BY = { vector: "Vector", fulltext: "Full text" } as const;
const HIT_COLUMNS: Column<SliceEstimate["hits"][number]>[] = [
  { key: "key", header: "Account key" },
  { key: "rrf", header: "Fused score", align: "right", render: (h) => h.rrf.toFixed(4) },
  { key: "found_by", header: "Found by", render: (h) => h.found_by.map((s) => FOUND_BY[s]).join(", ") },
];

const TRIM = {
  search: "Lower k or the hops, then estimate again.",
  schema: "Narrow the labels, the types or the window, then estimate again.",
  cypher: "Tighten the query, then estimate again.",
} as const;

export default function Slice() {
  const navigate = useNavigate();
  const [recipe, setRecipe] = useRecipe();
  const [, setRunId] = useRunId();

  const [mode, setMode] = useState<Mode>(() => (recipe.search ? "search" : recipe.pull.mode));
  const [fields, setFields] = useState<Fields>(() => fieldsFrom(recipe));
  const set = (patch: Partial<Fields>) => setFields((f) => ({ ...f, ...patch }));

  // The recipe on screen, kept in the run context so other screens and "Show code" see it.
  const draft = useMemo(() => draftOf(recipe, mode, fields), [recipe, mode, fields]);
  const key = stableJson(draft);
  useEffect(() => { setRecipe((prev) => draftOf(prev, mode, fields)); }, [mode, fields, setRecipe]);

  // An estimate or an error counts only for the recipe it was made for; edit anything and it is gone.
  const [done, setDone] = useState<{ key: string; value: SliceEstimate } | null>(null);
  const [problem, setProblem] = useState<{ key: string; title: string; text: string } | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [running, setRunning] = useState(false);
  const estimate = done && done.key === key ? done.value : null;
  const shownProblem = problem && problem.key === key ? problem : null;

  const kCheck = count(fields.k, 1);
  const hopsCheck = count(fields.hops, 0);
  const missing =
    mode === "cypher" && !fields.query.trim() ? "Write a query, or use the example."
    : mode === "search" && !fields.text.trim() && !fields.account.trim() ? "Give a sentence, an account key, or both."
    : mode === "search" && (kCheck.error || hopsCheck.error) ? "Fix k and the hops first."
    : null;

  const overCap = estimate?.over_cap === true;
  const runHint = missing ?? (!estimate ? "Estimate first. Run unlocks once you have seen the size."
    : overCap ? "Run is off while the slice is over the cap." : null);

  const doEstimate = async () => {
    const sent = draft, sentKey = key;
    setEstimating(true); setProblem(null);
    try { setDone({ key: sentKey, value: await api.sliceEstimate({ recipe: sent }) }); }
    catch (e) { setProblem({ key: sentKey, title: "The estimate did not come back", text: errorMessage(e) }); }
    finally { setEstimating(false); }
  };

  const doRun = async () => {
    const sentKey = key;
    setRunning(true); setProblem(null);
    try {
      const { run_id } = await api.startRun(draft);
      setRunId(run_id);
      navigate("/run");
    } catch (e) {
      setProblem({ key: sentKey, title: "The run did not start", text: errorMessage(e) });
    } finally {
      setRunning(false);
    }
  };

  return (
    <Page title="Slice" purpose="Choose the part of the graph the models work on. You see its size and memory before anything runs.">
      <Card title="How to slice">
        <Tabs tabs={TABS} value={mode} onChange={(m) => setMode(m as Mode)} label="Slice mode">
          {mode === "schema" && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">A whole period, every account in it. Leave a field empty and it means everything in the mapping.</p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Labels" value={fields.labels} onChange={(e) => set({ labels: e.target.value })}
                  placeholder="Account, Device" hint="Comma separated. Empty means every label in the mapping." />
                <Field label="Relationship types" value={fields.types} onChange={(e) => set({ types: e.target.value })}
                  placeholder="TX, USES_DEVICE" hint="Comma separated. Empty means every type in the mapping." />
                <Field label="Since" type="date" value={fields.since} onChange={(e) => set({ since: e.target.value })}
                  hint="Payments from this day on. Empty means no start." />
                <Field label="Until" type="date" value={fields.until} onChange={(e) => set({ until: e.target.value })}
                  hint="Payments up to this day. Empty means no end." />
              </div>
            </div>
          )}

          {mode === "cypher" && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">You already know the shape. The query runs on the master graph and must return nodes and relationships, or paths.</p>
              <Field as="textarea" label="Cypher query" rows={6} value={fields.query} spellCheck={false}
                onChange={(e) => set({ query: e.target.value })} className="font-mono"
                hint="The example pulls October's payments with both accounts. Replace it with your own." />
              <Button onClick={() => set({ query: EXAMPLE_QUERY })} disabled={fields.query === EXAMPLE_QUERY}>Use the example</Button>
            </div>
          )}

          {mode === "search" && (
            <div className="space-y-4">
              <Notice kind="info" title="Search needs the first pass">
                Search looks inside a store that has had the first pass: an embedding on every node and a summary on every
                account. A run does it right after the pull. If it has not run yet, slice by schema first. The search runs
                inside a broad slice, which is the By schema tab (everything in the mapping unless you narrowed it).
              </Notice>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Search text" value={fields.text} onChange={(e) => set({ text: e.target.value })}
                  placeholder="accounts moving money in a ring" hint="A sentence. Vector search finds it by meaning, full text by words." />
                <Field label="Account key" value={fields.account} onChange={(e) => set({ account: e.target.value })}
                  placeholder="A0011" hint="Or an account already flagged. Give text, a key, or both." />
                <Field label="Hits to keep (k)" type="number" min={1} step={1} value={fields.k} error={kCheck.error}
                  onChange={(e) => set({ k: e.target.value })} hint={`Default ${DEFAULT_K}. Empty uses the lab's setting.`} />
                <Field label="Account steps out from each hit (h)" type="number" min={0} step={1} value={fields.hops} error={hopsCheck.error}
                  onChange={(e) => set({ hops: e.target.value })} hint={`Default ${DEFAULT_HOPS}. A step is a payment or a shared identifier.`} />
              </div>
            </div>
          )}
        </Tabs>

        <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-4">
          <Button variant={estimate ? "secondary" : "primary"} onClick={doEstimate} busy={estimating}
            disabled={missing !== null || running}>
            Estimate
          </Button>
          <Button variant={estimate && !overCap ? "primary" : "secondary"} onClick={doRun} busy={running}
            disabled={runHint !== null || estimating}>
            Run this slice
          </Button>
          {runHint && <span className="text-sm text-slate-500">{runHint}</span>}
        </div>

        {shownProblem && <Notice kind="error" title={shownProblem.title}>{shownProblem.text}</Notice>}
      </Card>

      {estimate && (
        <Card title="Estimate" description="What this slice will be, before anything is pulled or run.">
          {estimate.over_cap && (
            <Notice kind="warning" title="Over the cap">
              This slice is {formatInt(estimate.nodes)} nodes and the lab will not run one over {formatInt(estimate.cap)}.{" "}
              {TRIM[estimate.mode]}
            </Notice>
          )}
          {estimate.note && <Notice kind="info">{estimate.note}</Notice>}
          {estimate.mode === "search" && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-slate-800">Hits</h3>
              <Table columns={HIT_COLUMNS} rows={estimate.hits} rowKey={(h) => h.key} caption="Search hits"
                empty="No hits. Try a different sentence or account key." />
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {estimate.hops != null && <Stat label="Hops" value={estimate.hops} />}
            <Stat label="Nodes" value={formatInt(estimate.nodes)} hint={`The cap is ${formatInt(estimate.cap)}`} />
            <Stat label="Relationships" value={formatInt(estimate.relationships)} />
            <Stat label="Memory" value={formatMB(estimate.memory_bytes)} />
          </div>
        </Card>
      )}
    </Page>
  );
}
