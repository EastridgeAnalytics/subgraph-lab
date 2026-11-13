// Models (pages 03, 04 and 06): choose the models the next run uses, and read the risk factors that score it. The one
// decision: which models run. Ticking a model puts it in the recipe in the run context, which Run, Show code and every
// other screen share. The risk factors are not chosen here: the recipe has no field for that, so every factor in the
// factor file scores on every run, and a factor that a model writes counts 0 when its model is not in the recipe.
//
// Weights and thresholds can be edited on this screen, and only on this screen. The lab has no route that saves them,
// because the factor file is where they live. The edits show what a change would do to each factor's share of the
// score and appear in the factor's own code panel, ready to paste into the file.
import { useState } from "react";
import { api } from "../api/client";
import type { Factor, ModelInfo, ModelsList } from "../api/types";
import { CodeDrawer, type CodeTab } from "../components/CodeDrawer";
import { formatScore, humanize } from "../components/score";
import { useLoad } from "../components/useLoad";
import { Button, Card, Notice, Page, Stat, Table, cx, type Column } from "../components/ui";
import { useRecipe } from "../state/run";

const FACTOR_FILE = "factors/factors.yaml";
const INPUT = "block w-24 rounded-md border bg-white px-2 py-1 text-right text-sm tabular-nums text-slate-900 " +
  "focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-indigo-600";

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
    window: typeof p.window === "string" ? p.window : null, minSize: num(community.min_size), weights,
  };
}

// ---- edits: text in the boxes, parsed only to show what they would do --------------------------------------------------
interface Edit { weight?: string; threshold?: string }
interface Parsed { value: number | null; error: string | null; edited: boolean }

function parseEdit(text: string | undefined, original: number | null | undefined, kind: "weight" | "threshold"): Parsed {
  const was = original ?? null;
  if (text === undefined) return { value: was, error: null, edited: false };
  const n = text.trim() === "" ? NaN : Number(text);
  if (!Number.isFinite(n)) return { value: was, error: "Enter a number.", edited: false };
  if (kind === "weight" && n < 0) return { value: was, error: "Use 0 or more.", edited: false };
  if (kind === "threshold" && n <= 0) return { value: was, error: "Use a number above 0.", edited: false };
  return { value: n, error: null, edited: n !== was };
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

function howItRuns(f: Factor, path: string): string {
  const head = f.kind === "cypher"
    ? [`# ${path}`, "# A .cypher file beside the factor file. It returns key, value and evidence_ids, and takes $threshold and $keys.",
       "# The factor's score is min(1, value / threshold)."]
    : f.kind === "python"
      ? [`# ${path}  (${f.function})`, "# Called as function(run, factor, keys). Returns a table of key, score and evidence."]
      : [`# ${path}`, `# The example writes artifacts/models/${f.id}.parquet for the run, and the lab reads it.`,
         "# A missing or skipped model counts 0 and keeps its weight in the divisor."];
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

  const purpose = "Choose the models the next run uses, and read the risk factors that score it. Weights and thresholds live in the factor file.";
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

  const toggle = (name: string) => setRecipe((prev) => {
    const wanted = prev.models.includes(name) ? prev.models.filter((m) => m !== name) : [...prev.models, name];
    const order = models.map((m) => m.name);
    const rank = (n: string) => (order.indexOf(n) < 0 ? order.length : order.indexOf(n));
    return { ...prev, models: [...wanted].sort((a, b) => rank(a) - rank(b)) };
  });

  const setEdit = (id: string, patch: Edit) => setEdits((e) => ({ ...e, [id]: { ...e[id], ...patch } }));
  const weightOf = (info: ModelInfo) => parseEdit(edits[info.name]?.weight, info.factor?.weight, "weight");
  const thresholdOf = (info: ModelInfo) => parseEdit(edits[info.name]?.threshold, info.factor?.threshold, "threshold");
  const total = factors.reduce((t, info) => t + (weightOf(info).value ?? 0), 0);
  const anyEdited = factors.some((info) => weightOf(info).edited || thresholdOf(info).edited);

  const columns: Column<ModelInfo>[] = [
    { key: "name", header: "Risk factor", render: (info) => {
      const f = info.factor;
      const feeder = f?.kind === "model" && f.source ? models.find((m) => m.code_path === f.source) : undefined;
      return (
        <div className="max-w-md space-y-1">
          <p className="font-medium text-slate-900">{f?.name ?? humanize(info.name)} <code className="text-xs font-normal text-slate-500">{info.name}</code></p>
          <p className="text-slate-600">{info.description}</p>
          {feeder && (ticked.has(feeder.name)
            ? <p className="text-xs text-slate-500">Written by the {feeder.name} model.</p>
            : <p className="text-xs font-medium text-amber-900">Counts 0 on the next run: the {feeder.name} model writes it, and it is not ticked.</p>)}
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
          <input id={id} type="number" step="0.01" min="0" aria-label={`Weight for ${info.name}`} value={edits[info.name]?.weight ?? String(info.factor.weight)}
            aria-invalid={w.error ? true : undefined} aria-describedby={w.error ? `${id}-error` : undefined}
            onChange={(e) => setEdit(info.name, { weight: e.target.value })} className={cx(INPUT, w.error ? "border-red-500" : "border-slate-300")} />
          {w.error && <span id={`${id}-error`} className="text-xs text-red-700">{w.error}</span>}
          {w.edited && <span className="text-xs font-medium text-amber-900">edited here, not saved</span>}
        </div>
      );
    } },
    { key: "share", header: "Share of the score", align: "right", render: (info) => {
      const w = weightOf(info).value;
      return w === null || total <= 0 ? "unknown" : `${Math.round((w / total) * 100)}%`;
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

  // What the open code panel shows.
  const openInfo = open ? data.models.find((m) => m.name === open.name && m.kind === open.kind) ?? null : null;
  let tabs: CodeTab[] = [];
  let title = "";
  if (openInfo && open?.kind === "model") {
    title = `Model: ${openInfo.name}`;
    tabs = [{ id: "recipe", label: "In the recipe", text: `# Add this under models: in the recipe, with the others you tick.\n# ${openInfo.description}\nmodels:\n- ${openInfo.name}\n` }];
  } else if (openInfo && open?.kind === "factor") {
    title = `Risk factor: ${openInfo.name}`;
    const f = openInfo.factor;
    if (f) {
      const w = weightOf(openInfo), t = thresholdOf(openInfo);
      tabs = [
        { id: "yaml", label: "Factor file entry", text: factorYaml(f, w.value ?? f.weight, t.value, { weight: w.edited, threshold: t.edited }) },
        { id: "how", label: "How the lab runs it", text: howItRuns(f, openInfo.code_path) },
      ];
    } else {
      tabs = [{ id: "path", label: "Where it lives", text: `${openInfo.code_path}\n` }];
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
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600" />
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

      <Card title="Risk factors" description={<>Every factor in the factor file (version <code>{data.factor_version}</code>) scores on every run. Each is scored 0 to 1, and the policy combines them into an account score.</>}>
        <Notice kind="info" title="Weights and thresholds are not saved from here">
          The factor file, <code>{FACTOR_FILE}</code> as shipped, is where they live, and the lab has no route that saves them. What you type here
          is for working out a change: it is sent nowhere and is gone when you leave. To change a weight for real, edit the file and run again.
        </Notice>
        <Table columns={columns} rows={factors} rowKey={(info) => info.name} caption="Risk factors" empty="The lab lists no risk factors." />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-slate-700">
            Weights add up to <span className="font-medium text-slate-900">{formatScore(total)}</span>{anyEdited ? " with your edits" : ""}.
            The policy takes a weighted mean, so each factor&apos;s share is its weight over that total. Changing weights moves every score, so check the alert threshold too.
          </p>
          {anyEdited && <Button onClick={() => setEdits({})}>Reset edits</Button>}
        </div>
      </Card>

      <Card title="Policy" description={<>How factors become scores. Read-only here: <code>policy.yaml</code> is where it lives.</>}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Alert threshold" value={policy.alert === null ? "unknown" : formatScore(policy.alert)} hint="An account at or above it gets an alert" />
          <Stat label="Combined by" value={policy.combine ? policy.combine.replace(/_/g, " ") : "unknown"} />
          <Stat label="Alert window" value={policy.window ?? "unknown"} hint="An ISO 8601 duration" />
          <Stat label="Smallest community scored" value={policy.minSize === null ? "unknown" : String(policy.minSize)} hint="Smaller groups are not scored or staged" />
        </div>
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-slate-800">Community score weights</h3>
          <p className="text-sm text-slate-600">A community&apos;s score is the weighted mean of these parts.</p>
          <Table columns={WEIGHT_COLUMNS} rows={policy.weights.map(([part, weight]) => ({ part, weight }))} rowKey={(r) => r.part}
            caption="Community score weights" empty="The policy gives no community weights." />
        </div>
      </Card>

      <CodeDrawer open={openInfo !== null} onClose={() => setOpen(null)} title={title} path={openInfo?.code_path} tabs={tabs}
        description={openInfo?.description}
        notice={<Notice kind="info">The lab has no route that returns a file&apos;s contents, so this panel shows where the code lives and how the lab uses it. Open the path in the repository to read the source.</Notice>} />
    </Page>
  );
}
