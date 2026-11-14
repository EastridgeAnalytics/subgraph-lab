// "Show code" without the lab: a recipe as YAML and as the Python that runs it, built in the browser.
// The YAML follows `Recipe.dump_yaml()` (lab/contracts/recipe.py): keys in order, no nulls, lists flush with their key.
// The Python uses the names in lab/runs.py. Only what is in the recipe is written; the lab fills in the rest.
import type { Recipe } from "../api/types";

/** The recipe without null and undefined properties, as `dump_yaml` writes it. */
function clean(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(clean);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => [k, clean(v)]));
  }
  return value;
}

// ---- YAML ---------------------------------------------------------------------------------------------------
const PLAIN = /^[A-Za-z_][A-Za-z0-9_./-]*(?: [A-Za-z0-9_./-]+)*$/;
const RESERVED = /^(true|false|null|yes|no|on|off|y|n)$/i;
const UNPRINTABLE = /[\u0000-\u0008\u000b-\u001f\u007f\r]/;

function scalar(v: string | number | boolean): string {
  if (typeof v !== "string") return String(v);
  if (v === "") return "''";
  return PLAIN.test(v) && !RESERVED.test(v) ? v : JSON.stringify(v);
}

// A multi-line string as a block scalar, or null when it is safer quoted.
function block(s: string): { header: string; lines: string[] } | null {
  if (!s.includes("\n") || UNPRINTABLE.test(s) || /^[ \t]/.test(s)) return null;
  const trailing = s.length - s.replace(/\n+$/, "").length;
  const header = trailing === 0 ? "|-" : trailing === 1 ? "|" : "|+";
  const body = trailing === 0 ? s : s.slice(0, s.length - trailing);
  const lines = body.split("\n");
  for (let i = 1; i < trailing; i++) lines.push("");
  return { header, lines };
}

function isEmptyCollection(v: unknown): boolean {
  return Array.isArray(v) ? v.length === 0 : typeof v === "object" && v !== null && Object.keys(v).length === 0;
}

// The value after "key:" or "- " when it fits on that line, else null.
function inline(v: unknown): string | null {
  if (typeof v === "string") return block(v) ? null : scalar(v);
  if (v !== null && typeof v === "object") return isEmptyCollection(v) ? (Array.isArray(v) ? "[]" : "{}") : null;
  return v === null || v === undefined ? "null" : scalar(v as number | boolean);
}

function lines(value: unknown, indent: number): string[] {
  const pad = " ".repeat(indent);
  const out: string[] = [];
  if (Array.isArray(value)) {
    for (const item of value) {
      const flat = inline(item);
      if (flat !== null) { out.push(`${pad}- ${flat}`); continue; }
      if (typeof item === "string") {
        const b = block(item)!;
        out.push(`${pad}- ${b.header}`, ...b.lines.map((l) => (l ? `${pad}  ${l}` : "")));
        continue;
      }
      const sub = lines(item, indent + 2);
      out.push(`${pad}- ${sub[0].slice(indent + 2)}`, ...sub.slice(1));
    }
    return out;
  }
  for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
    if (v === null || v === undefined) continue;
    const k = scalar(key);
    const flat = inline(v);
    if (flat !== null) { out.push(`${pad}${k}: ${flat}`); continue; }
    if (typeof v === "string") {
      const b = block(v)!;
      out.push(`${pad}${k}: ${b.header}`, ...b.lines.map((l) => (l ? `${pad}  ${l}` : "")));
    } else {
      out.push(`${pad}${k}:`, ...lines(v, Array.isArray(v) ? indent : indent + 2));
    }
  }
  return out;
}

export function recipeToYaml(recipe: Recipe): string {
  return lines(clean(recipe), 0).join("\n") + "\n";
}

// ---- Python -------------------------------------------------------------------------------------------------
function py(value: unknown, indent: number): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return JSON.stringify(value);
  const pad = " ".repeat(indent);
  const inner = " ".repeat(indent + 4);
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    if (value.every((v) => v === null || typeof v !== "object")) {
      const flat = `[${value.map((v) => py(v, 0)).join(", ")}]`;
      if (flat.length <= 60) return flat;
    }
    return `[\n${value.map((v) => `${inner}${py(v, indent + 4)},`).join("\n")}\n${pad}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return "{}";
  return `{\n${entries.map(([k, v]) => `${inner}${JSON.stringify(k)}: ${py(v, indent + 4)},`).join("\n")}\n${pad}}`;
}

export function recipeToPython(recipe: Recipe): string {
  const search = recipe.search
    ? "run.search()        # hybrid search, then account steps out from each hit"
    : "# no search in this recipe: the models work on the whole pull";
  return [
    "import lab",
    "from lab.contracts.recipe import Recipe",
    "",
    "# NEO4J_* come from .env",
    `recipe = Recipe.model_validate(${py(clean(recipe), 0)})`,
    "",
    "run = lab.Run.create(recipe)",
    "run.pull()          # the broad slice into a fresh run store",
    "run.first_pass()    # an embedding on every node, a template summary on every account",
    search,
    "run.score()         # the factors and the policy on the slice",
    "run.push()          # the dry run and the delta. run.push(confirm=True) is what writes",
    "",
  ].join("\n");
}
