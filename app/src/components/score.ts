// Small formatters for scores, amounts, keys and times, shared by the Results screen and its components.
// en-US on purpose, like format.ts, so the same number reads the same on every machine.
const TWO = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const AMOUNT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** A score from 0 to 1, two decimals: 0.77. */
export function formatScore(n: number): string {
  return TWO.format(n);
}

/** An amount of money as the lab sent it: grouped, at most two decimals, no currency (the lab does not say which). */
export function formatAmount(n: number): string {
  return AMOUNT.format(n);
}

/** A long key cut to `max` characters with an ellipsis. The full key belongs in a title or a code block beside it. */
export function shortKey(key: string, max = 12): string {
  return key.length > max ? `${key.slice(0, max)}…` : key;
}

/** The first `n` characters of a key, with no ellipsis: what a screen reader says for a long key without reading all of it. */
export function keyStart(key: string, n = 8): string {
  return key.slice(0, n);
}

/** The start of a text: whole words up to about `max` characters, then an ellipsis. */
export function startOf(text: string, max = 110): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max / 2 ? cut.slice(0, space) : cut).replace(/[\s.,;:]+$/, "")}…`;
}

/** An ISO time as "2026-10-03 14:02 UTC" when it is a UTC time, else as it came. */
export function formatWhen(iso?: string | null): string {
  if (!iso) return "unknown";
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|\+00:00)$/.exec(iso);
  return m ? `${m[1]} ${m[2]} UTC` : iso;
}

/** Hours as the evidence has them, so a number in a summary can be found: "48 hours", and "(2 days)" beside it from 48. */
export function formatHours(hours: number): string {
  const h = AMOUNT.format(Math.round(hours * 10) / 10);
  const text = `${h} ${h === "1" ? "hour" : "hours"}`;
  return hours >= 48 ? `${text} (${AMOUNT.format(Math.round((hours / 24) * 10) / 10)} days)` : text;
}

/** snake_case to words: new_accounts to "New accounts". */
export function humanize(id: string): string {
  const words = id.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : id;
}
