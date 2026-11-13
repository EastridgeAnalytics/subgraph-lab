// A table a reader can sort and page: the kit's Table plus a button in the header of every column that has a
// `sortValue`, and Previous / Next under the rows. Sorting and paging work on the rows it is given; a screen that was
// given only part of a list (the lab's first page) says so beside the table.
//
// Sorting: click a header to sort by it, again to reverse. A row with no value for the column goes last either way,
// and rows that tie keep the order they came in. The header carries `aria-sort`; the footer says which rows are shown.
import { useMemo, useState, type ReactNode } from "react";
import { Button, cx } from "./ui";

export interface DataColumn<Row> {
  key: string;
  header: string;
  render?: (row: Row) => ReactNode;
  align?: "left" | "right";
  /** Makes the column sortable: the number or text to sort a row by, or null when it has none. */
  sortValue?: (row: Row) => number | string | null | undefined;
  /** Which way the first click sorts. Scores and counts want "desc"; names and keys, "asc". */
  firstSort?: "asc" | "desc";
}

export interface Sort { key: string; dir: "asc" | "desc" }

function compare(a: number | string, b: number | string): number {
  return typeof a === "number" && typeof b === "number"
    ? a - b
    : String(a).localeCompare(String(b), "en", { numeric: true });
}

/** Rows ordered by a column. Pure, so it can be tested without a screen. */
export function sortRows<Row>(rows: Row[], column: DataColumn<Row> | undefined, dir: "asc" | "desc"): Row[] {
  const value = column?.sortValue;
  if (!value) return rows;
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: value(row) }))
    .sort((a, b) => {
      const an = a.v === null || a.v === undefined, bn = b.v === null || b.v === undefined;
      if (an || bn) return an === bn ? a.i - b.i : an ? 1 : -1;
      return compare(a.v as number | string, b.v as number | string) * sign || a.i - b.i;
    })
    .map((x) => x.row);
}

export function DataTable<Row extends object>({ columns, rows, rowKey, caption, noun = "rows", pageSize = 10, initialSort = null, empty = "Nothing to show yet." }: {
  columns: DataColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row, index: number) => string | number;
  /** Names the table for screen readers, and its pager. */
  caption: string;
  /** What the rows are, for the footer: "accounts". */
  noun?: string;
  pageSize?: number;
  initialSort?: Sort | null;
  empty?: ReactNode;
}) {
  const [sort, setSort] = useState<Sort | null>(initialSort);
  const [page, setPage] = useState(0);

  const column = sort ? columns.find((c) => c.key === sort.key) : undefined;
  const sorted = useMemo(() => (sort ? sortRows(rows, column, sort.dir) : rows), [rows, column, sort]);
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const at = Math.min(page, pages - 1);
  const first = at * pageSize;
  const visible = sorted.slice(first, first + pageSize);

  const choose = (c: DataColumn<Row>) => {
    setPage(0);
    setSort((prev) => (prev?.key === c.key
      ? { key: c.key, dir: prev.dir === "asc" ? "desc" : "asc" }
      : { key: c.key, dir: c.firstSort ?? "asc" }));
  };
  const cell = (row: Row, c: DataColumn<Row>): ReactNode =>
    c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "");
  const align = (c: DataColumn<Row>) => (c.align === "right" ? "text-right" : "text-left");

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-md border border-slate-200">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="bg-slate-50">
            <tr>
              {columns.map((c) => {
                const on = sort?.key === c.key;
                return (
                  <th key={c.key} scope="col" className={cx("px-4 py-2 font-medium text-slate-700", align(c))}
                    aria-sort={c.sortValue ? (on ? (sort!.dir === "asc" ? "ascending" : "descending") : "none") : undefined}>
                    {c.sortValue ? (
                      <button type="button" onClick={() => choose(c)}
                        className="inline-flex items-center gap-1 rounded font-medium hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">
                        {c.header}
                        <span aria-hidden="true" className={on ? "text-indigo-700" : "text-slate-400"}>
                          {on ? (sort!.dir === "asc" ? "▲" : "▼") : "↕"}
                        </span>
                      </button>
                    ) : c.header}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {visible.length === 0 ? (
              <tr><td colSpan={columns.length} className="px-4 py-6 text-center text-slate-500">{empty}</td></tr>
            ) : visible.map((row, i) => (
              <tr key={rowKey(row, first + i)} className="align-top">
                {columns.map((c) => <td key={c.key} className={cx("px-4 py-2 text-slate-800", align(c))}>{cell(row, c)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sorted.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-600">
          <p role="status">Showing {first + 1} to {first + visible.length} of {sorted.length} {noun}</p>
          {pages > 1 && (
            <nav aria-label={`Pages of ${caption}`} className="flex items-center gap-2">
              <Button onClick={() => setPage(at - 1)} disabled={at === 0}>Previous</Button>
              <span>Page {at + 1} of {pages}</span>
              <Button onClick={() => setPage(at + 1)} disabled={at >= pages - 1}>Next</Button>
            </nav>
          )}
        </div>
      )}
    </div>
  );
}
