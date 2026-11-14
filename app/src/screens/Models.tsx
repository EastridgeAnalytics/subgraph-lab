// Models (pages 03, 04 and 06): choose the models and the risk factors the next run uses, and read the code behind them.
// Two decisions, both in the recipe in the run context, which Run, Show code and every other screen share. Ticking a model
// puts it in `recipe.models`. Ticking a factor puts it in `recipe.factors` (decision 40): unticking one lists the others,
// and ticking them all again leaves `factors` unset, so every factor in the file runs, including ones added to the file
// later. A factor that is not chosen does not run and counts nothing. A factor that a model writes does nothing when its
// model is not in the recipe.
//
// The policy combines the factors by noisy-or (decision 39): a weight is how much its factor can say alone, from 0 to 1,
// and a factor that does not fire changes nothing. The policy's `combine` can still be weighted_mean, and then the old
// wording is used: a weight is a share of the score. The community score has its own `community.combine` (decisions 36
// and 45), and the community weights card says which it is.
//
// Weights and thresholds can be edited on this screen, and only on this screen. The lab has no route that saves them,
// because the factor file is where they live. The edits show what a change would do and appear in the factor's own code
// panel, ready to paste into the file. The code panel shows the real file, read from `GET /api/models/{name}/source`
// when it opens (decision 43).
import { useState } from "react";
import { api } from "../api/client";
import type { Factor, ModelInfo, ModelsList, Recipe, SourceFile } from "../api/types";
import { CodeDrawer, type CodeTab } from "../components/CodeDrawer";
import { formatScore, humanize } from "../components/score";
import { useLoad } from "../components/useLoad";
import { Button, Card, Notice, Page, Stat, Table, cx, type Column } from "../components/ui";
import { useRecipe } from "../state/run";

const FACTOR_FILE = "factors/factors.yaml";
const INPUT = "block w-24 rounded-md border bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 " +
  "focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-indigo-600";
const CHECKBOX = "h-4 w-4 rounded border-slate-300 text-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600";

// ---- what the policy says, read defensively: it is a free-form object in the contract ----------------------------------
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

function readPolicy(p: Record<string, unknown>) {
  const community = isObject(p.community) ? p.community : {};
  const weights = isObject(community.weights)
    ? Object.entries(community.weights).filter((e): e is [string, number] => num(e[1]) !== null)
    : [];
  return {
    alert: num(p.alert_threshold), combine: typeof p.combine === "string" ? p.combine : null,
    // How the community's parts combine (decision 45). A policy that says nothing is a weighted mean, as the lab reads it.
    communityCombine: community.combine === "noisy_or" ? "noisy_or" : "weighted_mean",
    window: typeof p.window === "string" ? p.window : null, minSize: num(community.min_size), weights,
  };
}

const COMBINE_NAMES: Record<string, string> = { noisy_or: "noisy-or", weighted_mean: "weighted mean" };

// ---- edits: text in the boxes, parsed only to show what they would do --------------------------------------------------
interface Edit { weight?: string; threshold?: string }
interface Parsed { value: number | null; error: string | null; edited: boolean }

/** A noisy-or weight is in [0, 1] (the lab refuses one outside it); a weighted-mean weight only has to be 0 or more. */
function parseEdit(text: string | undefined, original: number | null | undefined, kind: "weight" | "threshold", unitWeights: boolean): Parsed {
  const was = original ?? null;
  if (text === undefined) return { value: was, error: null, edited: false };
  const n = text.trim() === "" ? NaN : Number(text);
  if (!Number.isFinite(n)) return { value: was, error: "Enter a number.", edited: false };
  if (kind === "weight" && unitWeights && (n < 0 || n > 1)) return { value: was, error: "Use a number from 0 to 1.", edited: false };
  if (kind === "weight" && !unitWeights && n < 0) return { value: was, error: "Use 0 or more.", edited: false };
  if (kind === "threshold" && n <= 0) return { value: was, error: "Use a number above 0.", edited: false };
  return { value: n, error: null, edited: n !== was };
}

// ---- the recipe's factors ------------------------------------------------------------------------------------------------
/** The recipe with `factors` set, placed after `models` as `Recipe.dump_yaml()` writes it, or left out when null (every factor). */
function withFactors(recipe: Recipe, factors: string[] | null): Recipe {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(recipe)) {
    if (k === "factors") continue;
    out[k] = v;
    if (k === "models" && factors) out.factors = factors;
  }
  if (factors && !("factors" in out)) out.factors = factors;
  return out as unknown as Recipe;
}

// ---- the factor's code panel -------------------------------------------------------------------------------------------
function scalar(s: string): string {
  return /^[A-Za-z_][A-Za-z0-9_./:-]*$/.test(s) && !/^(true|false|null|yes|no|on|off|y|n)$/i.test(s) ? s : JSON.stringify(s);
}

function factorYaml(f: Factor, weight: number, threshold: number | null, edited: { weight: boolean; threshold: boolean }): string {
  const note = "  # edited on this screen, not saved";
  const lines = [`- id: ${scalar(f.id)}`, `  name: ${scalar(f.name)}`];
  if (f.description) lines.push(`  description: ${scalar(f.description)}`);
  lines.push(`  kind: ${f.kind}`);
  if (f.query) lines.push(`  query: ${scalar(f.query)}`);
  if (f.function) lines.push(`  function: ${scalar(f.function)}`);
  if (f.source) lines.push(`  source: ${scalar(f.source)}`);
  if (threshold !== null) lines.push(`  threshold: ${threshold}${edited.threshold ? note : ""}`);
  lines.push(`  weight: ${weight}${edited.weight ? note : ""}`);
  return lines.join("\n") + "\n";
}

function howItRuns(f: Factor, path: string, mean: boolean): string {
  const head = f.kind === "cypher"
    ? [`# ${path}`, "# A .cypher file beside the factor file. It returns key, value and evidence_ids, and takes $threshold and $keys.",
       "# The factor's score is min(1, value / threshold)."]
    : f.kind === "python"
      ? [`# ${path}  (${f.function})`, "# Called as function(run, factor, keys). Returns a table of key, score and evidence."]
      : [`# ${path}`, `# The example writes artifacts/models/${f.id}.parquet for the run, and the lab reads it.`,
         mean ? "# A missing or skipped model counts 0 and keeps its weight in the divisor."
              : "# A missing or skipped model changes nothing in the score."];
  return head.join("\n") + "\n";
}

const WEIGHT_COLUMNS: Column<{ part: string; weight: number }>[] = [
  { key: "part", header: "Part", render: (r) => humanize(r.part) },
  { key: "weight", header: "Weight", align: "right", render: (r) => formatScore(r.weight) },
];

type Open = { kind: "model" | "factor"; name: string } | null;

export default function Models() {
  const [recipe, setRecipe] = useRecipe();
  const { data, error, loading, reload } = useLoad<ModelsList>(() => api.models(), "models");
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [open, setOpen] = useState<Open>(null);
  // The real file behind the open panel, asked for each time a panel opens. Nothing is asked while none is open.
  const source = useLoad<SourceFile>(open ? () => api.source(open.name) : null, open ? `source|${open.kind}|${open.name}` : "source");

  const purpose = "Choose the models and risk factors the next run uses, and read the code behind them. Weights and thresholds live in the factor file.";
  if (!data) {
    return (
      <Page title="Models" purpose={purpose}>
        {error ? (
          <Notice kind="error" title="The models did not load">
            <p>{error}</p>
            <div className="mt-3"><Button onClick={reload}>Try again</Button></div>
          </Notice>
        ) : loading ? <p role="status" className="text-sm text-slate-600">Loading the models…</p> : null}
      </Page>
    );
  }

  const models = data.models.filter((m) => m.kind === "model");
  const factors = data.models.filter((m) => m.kind === "factor");
  const ticked = new Set(recipe.models);
  const policy = readPolicy(data.policy);
  const mean = policy.combine === "weighted_mean"; // anything else reads as the shipped noisy-or

  // Factors: unset means every factor in the file. The ids the file does not have are named, since a run would refuse them.
  const factorIds = factors.map((m) => m.name);
  const chosen = recipe.factors ?? null;
  const factorTicked = (id: string) => chosen === null || chosen.includes(id);
  const tickedFactors = factorIds.filter(factorTicked);
  const unknownChosen = (chosen ?? []).filter((id) => !factorIds.includes(id));

  const toggle = (name: string) => setRecipe((prev) => {
    const wanted = prev.models.includes(name) ? prev.models.filter((m) => m !== name) : [...prev.models, name];
    const order = models.map((m) => m.name);
    const rank = (n: string) => (order.indexOf(n) < 0 ? order.length : order.indexOf(n));
    return { ...prev, models: [...wanted].sort((a, b) => rank(a) - rank(b)) };
  });

  const toggleFactor = (id: string) => setRecipe((prev) => {
    const now = prev.factors == null ? factorIds : factorIds.filter((x) => prev.factors!.includes(x));
    const next = factorIds.filter((x) => (x === id ? !now.includes(id) : now.includes(x))); // in the file's order
    return withFactors(prev, next.length === factorIds.length ? null : next);
  });

  const setEdit = (id: string, patch: Edit) => setEdits((e) => ({ ...e, [id]: { ...e[id], ...patch } }));
  const weightOf = (info: ModelInfo) => parseEdit(edits[info.name]?.weight, info.factor?.weight, "weight", !mean);
  const thresholdOf = (info: ModelInfo) => parseEdit(edits[info.name]?.threshold, info.factor?.threshold, "threshold", !mean);
  const total = factors.filter((info) => factorTicked(info.name)).reduce((t, info) => t + (weightOf(info).value ?? 0), 0);
  const anyEdited = factors.some((info) => weightOf(info).edited || thresholdOf(info).edited);

  const columns: Column<ModelInfo>[] = [
    { key: "use", header: "Use", render: (info) => (
      <input type="checkbox" checked={factorTicked(info.name)} onChange={() => toggleFactor(info.name)} aria-label={`Use factor ${info.name}`} className={CHECKBOX} />
    ) },
    { key: "name", header: "Risk factor", render: (info) => {
      const f = info.factor;
      const feeder = f?.kind === "model" && f.source ? models.find((m) => m.code_path === f.source) : undefined;
      return (
        <div className="max-w-md space-y-1">
          <p className="font-medium text-slate-900">{f?.name ?? humanize(info.name)} <code className="text-xs font-normal text-slate-500">{info.name}</code></p>
          <p className="text-slate-600">{info.description}</p>
          {!factorTicked(info.name)
            ? <p className="text-xs font-medium text-amber-900">Not chosen: it does not run on the next run and counts nothing.</p>
            : feeder && (ticked.has(feeder.name)
              ? <p className="text-xs text-slate-500">Written by the {feeder.name} model.</p>
              : <p className="text-xs font-medium text-amber-900">
                  {mean ? "Counts 0" : "Changes nothing"} on the next run: the {feeder.name} model writes it, and it is not ticked.
                </p>)}
        </div>
      );
    } },
    { key: "kind", header: "Kind", render: (info) => info.factor?.kind ?? "unknown" },
    { key: "weight", header: "Weight", align: "right", render: (info) => {
      if (!info.factor) return "not given";
      const w = weightOf(info);
      const id = `weight-${info.name}`;
      return (
        <div className="flex flex-col items-end gap-1">
          <input id={id} type="number" step="0.01" min="0" max={mean ? undefined : "1"} aria-label={`Weight for ${info.name}`}
            value={edits[info.name]?.weight ?? String(info.factor.weight)}
            aria-invalid={w.error ? true : undefined} aria-describedby={w.error ? `${id}-error` : undefined}
            onChange={(e) => setEdit(info.name, { weight: e.target.value })} className={cx(INPUT, w.error ? "border-red-500" : "border-slate-300")} />
          {w.error && <span id={`${id}-error`} className="text-xs text-red-700">{w.error}</span>}
          {w.edited && <span className="text-xs font-medium text-amber-900">edited here, not saved</span>}
        </div>
      );
    } },
    mean
      // The old wording, for a policy that still takes a weighted mean: a weight is a share of the total.
      ? { key: "share", header: "Share of the score", align: "right", render: (info) => {
          const w = weightOf(info).value;
          if (!factorTicked(info.name)) return <span className="text-slate-500">not chosen</span>;
          return w === null || total <= 0 ? "unknown" : `${Math.round((w / total) * 100)}%`;
        } }
      // Noisy-or: the score an account gets when only this factor fires, at full strength, is its weight.
      : { key: "alone", header: "Alone it reaches", align: "right", render: (info) => {
          const w = weightOf(info).value;
          if (w === null) return "unknown";
          return (
            <div className="space-y-0.5">
              <p className="tabular-nums text-slate-900">{formatScore(w)}</p>
              {policy.alert !== null && (
                <p className={cx("text-xs", w >= policy.alert ? "font-medium text-slate-700" : "text-slate-500")}>
                  {w >= policy.alert ? "Alerts on its own" : "Cannot alert on its own"}
                </p>
              )}
            </div>
          );
        } },
    { key: "threshold", header: "Threshold", align: "right", render: (info) => {
      const f = info.factor;
      if (!f || f.threshold == null) return <span className="text-slate-500">none</span>;
      const t = thresholdOf(info);
      const id = `threshold-${info.name}`;
      return (
        <div className="flex flex-col items-end gap-1">
          <input id={id} type="number" step="any" aria-label={`Threshold for ${info.name}`} value={edits[info.name]?.threshold ?? String(f.threshold)}
            aria-invalid={t.error ? true : undefined} aria-describedby={t.error ? `${id}-error` : undefined}
            onChange={(e) => setEdit(info.name, { threshold: e.target.value })} className={cx(INPUT, t.error ? "border-red-500" : "border-slate-300")} />
          {t.error && <span id={`${id}-error`} className="text-xs text-red-700">{t.error}</span>}
          {t.edited && <span className="text-xs font-medium text-amber-900">edited here, not saved</span>}
        </div>
      );
    } },
    { key: "code", header: "Code", render: (info) => (
      <Button className="whitespace-nowrap" aria-label={`Show code for factor ${info.name}`} onClick={() => setOpen({ kind: "factor", name: info.name })}>Show code</Button>
    ) },
  ];

  // What the open code panel shows: the real file first, then what is worked out on this screen.
  const openInfo = open ? data.models.find((m) => m.name === open.name && m.kind === open.kind) ?? null : null;
  let tabs: CodeTab[] = [];
  let title = "";
  if (openInfo && open) {
    const file = source.data;
    tabs = [{
      id: "source", label: file?.path ?? openInfo.code_path, language: file?.language,
      text: file ? file.text : source.error ? "The code did not load." : "Loading the code…",
    }];
    if (open.kind === "model") {
      title = `Model: ${openInfo.name}`;
      tabs.push({ id: "recipe", label: "In the recipe", language: "yaml",
        text: `# Add this under models: in the recipe, with the others you tick.\n# ${openInfo.description}\nmodels:\n- ${openInfo.name}\n` });
    } else {
      title = `Risk factor: ${openInfo.name}`;
      const f = openInfo.factor;
      if (f) {
        const w = weightOf(openInfo), t = thresholdOf(openInfo);
        tabs.push(
          { id: "yaml", label: "Factor file entry", language: "yaml", text: factorYaml(f, w.value ?? f.weight, t.value, { weight: w.edited, threshold: t.edited }) },
          { id: "how", label: "How the lab runs it", text: howItRuns(f, openInfo.code_path, mean) },
        );
      }
    }
  }

  return (
    <Page title="Models" purpose={purpose}>
      <Card title="Models" description="Tick the models the next run uses. Your choice goes into the recipe, which every screen shares.">
        <ul className="divide-y divide-slate-100">
          {models.map((m) => {
            const id = `model-${m.name}`;
            return (
              <li key={m.name} className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <div className="flex min-w-0 flex-1 items-start gap-3">
                  <input id={id} type="checkbox" checked={ticked.has(m.name)} onChange={() => toggle(m.name)} aria-describedby={`${id}-about`}
                    className={cx("mt-1", CHECKBOX)} />
                  <div className="min-w-0">
                    <label htmlFor={id} className="font-medium text-slate-900">{m.name}</label>
                    <p id={`${id}-about`} className="text-sm text-slate-600">{m.description}</p>
                    <p className="text-xs text-slate-500">Code: <code>{m.code_path}</code></p>
                  </div>
                </div>
                <Button aria-label={`Show code for model ${m.name}`} onClick={() => setOpen({ kind: "model", name: m.name })}>Show code</Button>
              </li>
            );
          })}
        </ul>
        <p role="status" className="text-sm text-slate-700">
          {recipe.models.length > 0 ? <>The next run uses: <span className="font-medium text-slate-900">{recipe.models.join(", ")}</span>.</> : "No model is ticked, so the next run will have no model output."}
        </p>
      </Card>

      <Card title="Risk factors" description={<>Tick the factors the next run scores with. With every factor ticked, the recipe names none, so every factor in the factor file (version <code>{data.factor_version}</code>) runs, including any added later. Each is scored 0 to 1, and the policy combines them into an account score.</>}>
        <Notice kind="info" title="Weights and thresholds are not saved from here">
          The factor file, <code>{FACTOR_FILE}</code> as shipped, is where they live, and the lab has no route that saves them. What you type here
          is for working out a change: it is sent nowhere and is gone when you leave. To change a weight for real, edit the file and run again.
        </Notice>
        {unknownChosen.length > 0 && (
          <Notice kind="warning" title="The recipe names a factor the file does not have">
            <code>{unknownChosen.join(", ")}</code>: a run with it stops with an error that names it. Tick or untick any factor here to drop it.
          </Notice>
        )}
        <Table columns={columns} rows={factors} rowKey={(info) => info.name} caption="Risk factors" empty="The lab lists no risk factors." />
        <p role="status" className="text-sm text-slate-700">
          {chosen === null
            ? "The next run scores with every factor in the file."
            : tickedFactors.length > 0
              ? <>The next run scores with: <span className="font-medium text-slate-900">{tickedFactors.join(", ")}</span>. The others are reported not chosen and do not run.</>
              : "No factor is ticked, so no factor scores anything on the next run."}
        </p>
        <div className="flex flex-wrap items-center justify-between gap-3">
          {mean ? (
            <p className="text-sm text-slate-700">
              Weights add up to <span className="font-medium text-slate-900">{formatScore(total)}</span>{anyEdited ? " with your edits" : ""}.
              The policy takes a weighted mean, so each factor&apos;s share is its weight over that total. Changing weights moves every score, so check the alert threshold too.
            </p>
          ) : (
            <p className="text-sm text-slate-700">
              The policy combines the factors by noisy-or. A weight is how much its factor can say alone, from 0 to 1, and it is the score an account gets
              when only that factor fires at full strength. A factor that does not fire changes nothing, and adding a factor can only raise a score.{" "}
              {policy.alert === null ? "An account alerts at the policy's alert threshold." : `An account alerts at ${formatScore(policy.alert)} or above.`}
            </p>
          )}
          {anyEdited && <Button onClick={() => setEdits({})}>Reset edits</Button>}
        </div>
      </Card>

      <Card title="Policy" description={<>How factors become scores. Read-only here: <code>policy.yaml</code> is where it lives.</>}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Alert threshold" value={policy.alert === null ? "unknown" : formatScore(policy.alert)} hint="An account at or above it gets an alert" />
          <Stat label="Account score combined by" value={policy.combine ? COMBINE_NAMES[policy.combine] ?? policy.combine.replace(/_/g, " ") : "unknown"}
            hint={policy.combine === "noisy_or" ? "A factor that does not fire changes nothing" : undefined} />
          <Stat label="Alert window" value={policy.window ?? "unknown"} hint="An ISO 8601 duration" />
          <Stat label="Smallest community scored" value={policy.minSize === null ? "unknown" : String(policy.minSize)} hint="Smaller groups are not scored or staged" />
        </div>
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-slate-800">Community score weights</h3>
          <p className="text-sm text-slate-600">
            {policy.communityCombine === "noisy_or"
              ? "A community's score combines these parts by noisy-or. Each weight is how much that part alone can say, from 0 to 1."
              : "A community's score is the weighted mean of these parts."}
          </p>
          <Table columns={WEIGHT_COLUMNS} rows={policy.weights.map(([part, weight]) => ({ part, weight }))} rowKey={(r) => r.part}
            caption="Community score weights" empty="The policy gives no community weights." />
        </div>
      </Card>

      <CodeDrawer open={openInfo !== null} onClose={() => setOpen(null)} title={title} path={source.data?.path ?? openInfo?.code_path} tabs={tabs}
        description={openInfo?.description}
        notice={(
          <>
            {source.error && (
              <Notice kind="error" title="The code did not load">
                <p>{source.error}</p>
                <div className="mt-3"><Button onClick={source.reload}>Try again</Button></div>
              </Notice>
            )}
            <Notice kind="info">This is the file as the lab holds it, read only. To change it, edit the file and run again.</Notice>
          </>
        )} />
    </Page>
  );
}
