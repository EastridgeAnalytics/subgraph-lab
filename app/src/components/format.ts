// Small formatters shared by the screens. en-US on purpose, so the same number reads the same on every machine.
const INT = new Intl.NumberFormat("en-US");

export function formatInt(n: number): string {
  return INT.format(n);
}

const MB = (digits: number) => new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const MB_ONE = MB(1);
const MB_TWO = MB(2);

/** Bytes as MB (1 MB = 1,000,000 bytes), one decimal, two under 1 MB. */
export function formatMB(bytes: number): string {
  const mb = bytes / 1_000_000;
  return `${(mb < 1 ? MB_TWO : MB_ONE).format(mb)} MB`;
}

/** The text to show for anything caught from an API call. */
export function errorMessage(e: unknown): string {
  if (e instanceof Error && e.message) return e.message;
  return typeof e === "string" && e ? e : "Something went wrong and the lab gave no reason.";
}
