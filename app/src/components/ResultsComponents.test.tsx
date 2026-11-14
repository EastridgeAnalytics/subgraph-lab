import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import type { GraphEdge, GraphNode, Summary } from "../api/types";
import { CodeDrawer } from "./CodeDrawer";
import { CommunityGraph, GRAPH_CAP } from "./CommunityGraph";
import { DataTable, sortRows, type DataColumn } from "./DataTable";
import { DispositionButtons } from "./DispositionButtons";
import { layoutGraph } from "./graphLayout";
import { ScoreParts } from "./ScoreParts";
import { formatAmount, formatHours, formatScore, formatWhen, humanize, keyStart, shortKey, startOf } from "./score";
import { SummaryBlock, wasReplaced } from "./SummaryBlock";
import { useLoad } from "./useLoad";
import { useStoredText } from "./useStoredText";

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

// ---- score.ts ------------------------------------------------------------------------------------------------------------
describe("score formatters", () => {
  test("scores have two decimals, amounts are grouped, long keys are cut", () => {
    expect(formatScore(0.766)).toBe("0.77");
    expect(formatScore(1)).toBe("1.00");
    expect(formatAmount(48200)).toBe("48,200");
    expect(formatAmount(1234.5678)).toBe("1,234.57");
    expect(shortKey("0123456789abcdef", 12)).toBe("0123456789ab…");
    expect(shortKey("short", 12)).toBe("short");
    expect(keyStart("0123456789abcdef")).toBe("01234567");
    expect(humanize("new_accounts")).toBe("New accounts");
  });

  test("a text is cut on a word, and short ones are left alone", () => {
    expect(startOf("A short one.")).toBe("A short one.");
    const cut = startOf("one two three four five six seven eight nine ten eleven twelve", 30);
    expect(cut).toBe("one two three four five six…");
    expect(startOf("a\n\nb   c")).toBe("a b c");
  });

  test("times and hours read as the evidence has them, so a number in a summary can be found", () => {
    expect(formatWhen("2026-10-03T14:02:00Z")).toBe("2026-10-03 14:02 UTC");
    expect(formatWhen("2026-10-03T14:02:00+02:00")).toBe("2026-10-03T14:02:00+02:00");
    expect(formatWhen(null)).toBe("unknown");
    expect(formatHours(1)).toBe("1 hour");
    expect(formatHours(30.25)).toBe("30.3 hours");
    expect(formatHours(48)).toBe("48 hours (2 days)");
    expect(formatHours(60)).toBe("60 hours (2.5 days)");
  });
});

// ---- DataTable ---------------------------------------------------------------------------------------------------------
interface Row { name: string; n: number | null }
const COLUMNS: DataColumn<Row>[] = [
  { key: "name", header: "Name", sortValue: (r) => r.name },
  { key: "n", header: "Count", sortValue: (r) => r.n, firstSort: "desc", align: "right" },
  { key: "plain", header: "Plain", render: () => "x" },
];
const ROWS: Row[] = [{ name: "b2", n: 5 }, { name: "a10", n: null }, { name: "a9", n: 5 }, { name: "c", n: 1 }];

describe("DataTable", () => {
  test("sortRows puts rows with no value last either way, keeps ties in their order, and sorts numbers in text by value", () => {
    expect(sortRows(ROWS, COLUMNS[1], "desc").map((r) => r.name)).toEqual(["b2", "a9", "c", "a10"]);
    expect(sortRows(ROWS, COLUMNS[1], "asc").map((r) => r.name)).toEqual(["c", "b2", "a9", "a10"]);
    expect(sortRows(ROWS, COLUMNS[0], "asc").map((r) => r.name)).toEqual(["a9", "a10", "b2", "c"]);
    expect(sortRows(ROWS, COLUMNS[2], "asc")).toBe(ROWS); // no sortValue: untouched
  });

  test("it pages, sorts from a header, and says which rows are shown", () => {
    const many = Array.from({ length: 25 }, (_, i) => ({ name: `row${String(i).padStart(2, "0")}`, n: i }));
    render(<DataTable columns={COLUMNS} rows={many} rowKey={(r) => r.name} caption="Things" noun="things" pageSize={10} />);
    const table = screen.getByRole("table", { name: "Things" });
    expect(within(table).getAllByRole("row")).toHaveLength(11);
    expect(screen.getByText("Showing 1 to 10 of 25 things")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Plain" })).not.toHaveAttribute("aria-sort");
    expect(within(table).getByRole("columnheader", { name: "Count" })).toHaveAttribute("aria-sort", "none");

    fireEvent.click(within(table).getByRole("button", { name: "Count" })); // first click is descending for this column
    expect(within(table).getByRole("columnheader", { name: "Count" })).toHaveAttribute("aria-sort", "descending");
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("row24");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Showing 21 to 25 of 25 things")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fireEvent.click(within(table).getByRole("button", { name: "Count" })); // reverse, and back to page one
    expect(screen.getByText("Showing 1 to 10 of 25 things")).toBeInTheDocument();
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("row00");
  });

  test("no rows shows the empty text and no pager, and one page shows no pager", () => {
    const { rerender } = render(<DataTable columns={COLUMNS} rows={[]} rowKey={(r) => r.name} caption="Things" empty="Nothing here." />);
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    rerender(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.name} caption="Things" />);
    expect(screen.getByText("Showing 1 to 4 of 4 rows")).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  test("when the rows shrink under the page the table is on, it shows the last page that is left", () => {
    const many = Array.from({ length: 25 }, (_, i) => ({ name: `row${i}`, n: i }));
    const { rerender } = render(<DataTable columns={COLUMNS} rows={many} rowKey={(r) => r.name} caption="Things" />);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    rerender(<DataTable columns={COLUMNS} rows={many.slice(0, 12)} rowKey={(r) => r.name} caption="Things" />);
    expect(screen.getByText("Showing 11 to 12 of 12 rows")).toBeInTheDocument();
  });

  test("footer false leaves out the range and the pager, for a screen that pages through the server", () => {
    const many = Array.from({ length: 25 }, (_, i) => ({ name: `row${String(i).padStart(2, "0")}`, n: i }));
    render(<DataTable columns={COLUMNS} rows={many} rowKey={(r) => r.name} caption="Things" pageSize={100} footer={false} />);
    expect(within(screen.getByRole("table", { name: "Things" })).getAllByRole("row")).toHaveLength(26); // every row, no pages
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Count" })); // it still sorts
    expect(within(screen.getByRole("table", { name: "Things" })).getAllByRole("row")[1]).toHaveTextContent("row24");
  });
});

// ---- DispositionButtons ------------------------------------------------------------------------------------------------
describe("DispositionButtons", () => {
  test("three buttons in a named group; the decision on record is pressed and spelled out", () => {
    const choose = vi.fn();
    render(<DispositionButtons subject="A1" value="false_positive" by="Tim" onChoose={choose} />);
    const group = screen.getByRole("group", { name: "Decision for A1" });
    expect(within(group).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual(
      ["Mark A1 as confirmed", "Mark A1 as false positive", "Mark A1 as needs more"]);
    expect(within(group).getByRole("button", { name: "Mark A1 as false positive" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "Mark A1 as confirmed" })).toHaveAttribute("aria-pressed", "false");
    expect(group.parentElement).toHaveTextContent("False positive by Tim. Staged: it will travel with the next push.");
    fireEvent.click(within(group).getByRole("button", { name: "Mark A1 as needs more" }));
    expect(choose).toHaveBeenCalledWith("needs_more");
  });

  test("with no decision it says nothing; saving and a failure say so; off says why on each button", () => {
    const { rerender } = render(<DispositionButtons subject="A1" onChoose={() => {}} />);
    expect(screen.queryByText(/Staged/)).not.toBeInTheDocument();
    rerender(<DispositionButtons subject="A1" saving onChoose={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");
    expect(screen.getAllByRole("button").every((b) => b.hasAttribute("disabled"))).toBe(true);
    rerender(<DispositionButtons subject="A1" error="boom" onChoose={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Not saved: boom");
    rerender(<DispositionButtons subject="A1" disabledReason="Add your name." onChoose={() => {}} />);
    for (const b of screen.getAllByRole("button")) { expect(b).toBeDisabled(); expect(b).toHaveAttribute("title", "Add your name."); }
  });

  test("a decision recorded but not staged does not claim it will travel", () => {
    render(<DispositionButtons subject="A1" value="confirmed" staged={false} onChoose={() => {}} />);
    expect(screen.getByText(/Recorded, but not staged/)).toBeInTheDocument();
    expect(screen.queryByText(/will travel/)).not.toBeInTheDocument();
  });
});

// ---- ScoreParts --------------------------------------------------------------------------------------------------------
describe("ScoreParts", () => {
  const parts = { member_risk: 0.8, cycles: 0.4 };
  const weights = { member_risk: 0.6, cycles: 0.4 };

  test("each part is a bar with its value, and with weights it shows value x weight and checks the sum against the score", () => {
    render(<ScoreParts parts={parts} weights={weights} score={0.64} />);
    expect(screen.getByRole("meter", { name: "Member risk" })).toHaveAttribute("aria-valuenow", "0.8");
    expect(screen.getByRole("meter", { name: "Cycles" })).toHaveAttribute("aria-valuetext", "0.40");
    expect(screen.getByText("× 0.60 = 0.48")).toBeInTheDocument();
    expect(screen.getByText("× 0.40 = 0.16")).toBeInTheDocument();
    expect(screen.getByText(/The parts add up to 0.64, the score the lab stored \(0.64\)\./)).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  test("when the policy's weights no longer give the stored score it says so and does not pretend", () => {
    render(<ScoreParts parts={parts} weights={weights} score={0.9} />);
    expect(screen.getByRole("note")).toHaveTextContent("the parts add up to 0.64, not the 0.90 the lab stored");
    expect(screen.getByRole("note")).toHaveTextContent("read the parts, not the sum");
  });

  test("weights that do not add to one are used as shares", () => {
    render(<ScoreParts parts={{ a: 1, b: 0 }} weights={{ a: 3, b: 1 }} score={0.75} />);
    expect(screen.getByText("× 0.75 = 0.75")).toBeInTheDocument();
    expect(screen.getByText(/The parts add up to 0.75/)).toBeInTheDocument();
  });

  test("without weights the bars stand alone; with a missing weight the sum is left out; with no parts it says so", () => {
    const { rerender } = render(<ScoreParts parts={parts} />);
    expect(screen.getAllByRole("meter")).toHaveLength(2);
    expect(screen.queryByText(/×/)).not.toBeInTheDocument();
    expect(screen.queryByText(/add up/)).not.toBeInTheDocument();
    rerender(<ScoreParts parts={parts} weights={{ member_risk: 1 }} score={0.8} />);
    expect(screen.getByText(/no weight for every part/)).toBeInTheDocument();
    rerender(<ScoreParts parts={{}} />);
    expect(screen.getByText("The lab gave no parts for this score.")).toBeInTheDocument();
  });
});

describe("ScoreParts: how the community formed, and noisy-or (decisions 42 and 45)", () => {
  const five = { member_risk: 0.8, shared_identifiers: 0.5, cycles: 0.4, new_accounts: 0.5, speed: 0.4 };
  const noisyWeights = { member_risk: 0.65, shared_identifiers: 0.2, cycles: 0.2, new_accounts: 0.2, speed: 0.2 };
  const noisyScore = 1 - (1 - 0.65 * 0.8) * (1 - 0.2 * 0.5) * (1 - 0.2 * 0.4) * (1 - 0.2 * 0.5) * (1 - 0.2 * 0.4); // 0.6709...
  const meanWeights = { member_risk: 0.35, shared_identifiers: 0.2, cycles: 0.15, new_accounts: 0.15, speed: 0.15 };

  test("formed_by_identifier is a tag, never a bar, and the five parts still add up to the stored score", () => {
    const stored = 0.8 * 0.35 + 0.5 * 0.2 + 0.4 * 0.15 + 0.5 * 0.15 + 0.4 * 0.15; // 0.575
    render(<ScoreParts parts={{ ...five, formed_by_identifier: 1 }} weights={meanWeights} score={stored} />);
    expect(screen.getByText("Formed by a shared identifier")).toBeInTheDocument();
    expect(screen.queryByText("Formed by payments")).not.toBeInTheDocument();
    expect(screen.getAllByRole("meter").map((m) => m.getAttribute("aria-label"))).toEqual(
      ["Member risk", "Shared identifiers", "Cycles", "New accounts", "Speed"]);
    expect(screen.queryByRole("meter", { name: /formed/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Formed by identifier/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/×/)).toHaveLength(5); // five rows with a weight, none for the tag
    expect(screen.getByText(/The parts add up to 0.58, the score the lab stored \(0.58\)\./)).toBeInTheDocument();
    expect(screen.queryByText(/no weight for every part/)).not.toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  test("a community that formed by payments says so, and 0 is not a missing tag", () => {
    render(<ScoreParts parts={{ ...five, formed_by_identifier: 0 }} weights={meanWeights} />);
    expect(screen.getByText("Formed by payments")).toBeInTheDocument();
    expect(screen.queryByText("Formed by a shared identifier")).not.toBeInTheDocument();
    expect(screen.getAllByRole("meter")).toHaveLength(5);
  });

  test("with no tag in the parts there is no line about how it formed", () => {
    render(<ScoreParts parts={five} weights={meanWeights} />);
    expect(screen.queryByText(/^Formed by/)).not.toBeInTheDocument();
  });

  test("with noisy_or each row is value x weight with the weight as it is, and the foot is 1 - the product of (1 - w v)", () => {
    render(<ScoreParts parts={{ ...five, formed_by_identifier: 1 }} weights={noisyWeights} combine="noisy_or" score={noisyScore} />);
    expect(screen.getByText("× 0.65 = 0.52")).toBeInTheDocument(); // 0.8 x 0.65, not a share of the total weight
    expect(screen.getAllByText("× 0.20 = 0.10")).toHaveLength(2); // 0.5 x 0.2, twice
    expect(screen.getAllByText("× 0.20 = 0.08")).toHaveLength(2); // 0.4 x 0.2, twice
    expect(screen.getByText(/The parts combine as 1 − Π\(1 − w·v\) = 0\.67, the score the lab stored \(0\.67\)\./)).toBeInTheDocument();
    expect(screen.queryByText(/add up to/)).not.toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.getAllByRole("meter")).toHaveLength(5);
    expect(screen.getByText("Formed by a shared identifier")).toBeInTheDocument();
  });

  test("noisy_or: a part that is 0 changes nothing, and when the stored score differs the foot says so and does not pretend", () => {
    const { rerender } = render(<ScoreParts parts={{ ...five, speed: 0 }} weights={noisyWeights} combine="noisy_or"
      score={1 - (1 - 0.65 * 0.8) * (1 - 0.2 * 0.5) * (1 - 0.2 * 0.4) * (1 - 0.2 * 0.5)} />);
    expect(screen.getByText(/combine as 1 − Π\(1 − w·v\) = 0\.64, the score the lab stored/)).toBeInTheDocument();
    rerender(<ScoreParts parts={five} weights={noisyWeights} combine="noisy_or" score={0.9} />);
    expect(screen.getByRole("note")).toHaveTextContent("the parts combine as 1 − Π(1 − w·v) = 0.67, not the 0.90 the lab stored");
    expect(screen.getByRole("note")).toHaveTextContent("read the parts, not the result");
  });

  test("weighted_mean, or no combine given, keeps the share and the sum", () => {
    const { rerender } = render(<ScoreParts parts={five} weights={meanWeights} combine="weighted_mean" score={0.58} />);
    expect(screen.getByText(/The parts add up to 0.58/)).toBeInTheDocument();
    expect(screen.queryByText(/Π/)).not.toBeInTheDocument();
    rerender(<ScoreParts parts={five} weights={meanWeights} combine={null} score={0.58} />);
    expect(screen.getByText(/The parts add up to 0.58/)).toBeInTheDocument();
    expect(screen.getByText("× 0.35 = 0.28")).toBeInTheDocument();
  });

  test("noisy_or with a weight missing leaves the result out and says so", () => {
    render(<ScoreParts parts={five} weights={{ member_risk: 0.65 }} combine="noisy_or" score={0.7} />);
    expect(screen.getByText(/no weight for every part, so the result is not shown/)).toBeInTheDocument();
    expect(screen.queryByText(/combine as/)).not.toBeInTheDocument();
  });

  test("only the tag and no parts says so, and still shows the tag", () => {
    render(<ScoreParts parts={{ formed_by_identifier: 1 }} />);
    expect(screen.getByText("Formed by a shared identifier")).toBeInTheDocument();
    expect(screen.getByText("The lab gave no parts for this score.")).toBeInTheDocument();
  });

  test("speed and cycles are described the way the lab defines them", () => {
    render(<ScoreParts parts={five} />);
    expect(screen.getByText("Money in and out of similar size within a day: the in_and_out_within_a_day factor's rows.")).toBeInTheDocument();
    expect(screen.getByText("Money that comes back to where it started, each payment after the one before it.")).toBeInTheDocument();
    expect(screen.queryByText(/within hours/)).not.toBeInTheDocument();
  });
});

// ---- the graph ---------------------------------------------------------------------------------------------------------
const account = (id: string, risk?: number): GraphNode => ({ id, label: "Account", props: risk === undefined ? {} : { risk_score: risk } });
const identifier = (id: string, kind = "device"): GraphNode => ({ id: `Device:${id}`, label: "Device", props: { key: id, kind } });
const pay = (source: string, target: string, payments = 1, amount = 100): GraphEdge => ({ source, target, type: "TX", props: { payments, amount } });

describe("layoutGraph", () => {
  const nodes = Array.from({ length: 12 }, (_, i) => ({ id: `n${i}` }));
  const edges = nodes.map((n, i) => ({ source: n.id, target: nodes[(i + 1) % nodes.length].id }));

  test("the same graph lands in the same place every time, inside the box", () => {
    const a = layoutGraph(nodes, edges, { width: 600, height: 300, padding: 20 });
    const b = layoutGraph(nodes, edges, { width: 600, height: 300, padding: 20 });
    expect([...a.entries()]).toEqual([...b.entries()]);
    expect(a.size).toBe(12);
    for (const p of a.values()) {
      expect(p.x).toBeGreaterThanOrEqual(19.9); expect(p.x).toBeLessThanOrEqual(580.1);
      expect(p.y).toBeGreaterThanOrEqual(19.9); expect(p.y).toBeLessThanOrEqual(280.1);
    }
  });

  test("no node sits on another, and linked nodes end up nearer than the farthest pair", () => {
    const at = layoutGraph(nodes, edges);
    const d = (a: string, b: string) => Math.hypot(at.get(a)!.x - at.get(b)!.x, at.get(a)!.y - at.get(b)!.y);
    let far = 0;
    for (const a of nodes) for (const b of nodes) if (a.id < b.id) { expect(d(a.id, b.id)).toBeGreaterThan(5); far = Math.max(far, d(a.id, b.id)); }
    expect(d("n0", "n1")).toBeLessThan(far);
  });

  test("nothing, one node, edges to nodes that are not there, and nodes with no links all lay out", () => {
    expect(layoutGraph([], []).size).toBe(0);
    expect(layoutGraph([{ id: "a" }], [], { width: 100, height: 50 }).get("a")).toEqual({ x: 50, y: 25 });
    const odd = layoutGraph([{ id: "a" }, { id: "b" }, { id: "c" }], [{ source: "a", target: "zzz" }, { source: "a", target: "a" }]);
    expect(odd.size).toBe(3);
    for (const p of odd.values()) { expect(Number.isFinite(p.x)).toBe(true); expect(Number.isFinite(p.y)).toBe(true); }
  });
});

describe("CommunityGraph", () => {
  const small: GraphNode[] = [account("A1", 0.9), account("A2", 0.5), account("A3"), identifier("D-1")];
  const smallEdges: GraphEdge[] = [pay("A1", "A2", 3, 500), pay("A2", "A1", 1, 100), pay("A2", "A3"),
    { source: "A1", target: "Device:D-1", type: "USES_DEVICE", props: {} }, { source: "A2", target: "Device:D-1", type: "USES_DEVICE", props: {} }];

  test("accounts are circles shaded by score, identifiers are squares, payments are arrows, and the picture has a name", () => {
    const { container } = render(<CommunityGraph nodes={small} edges={smallEdges} name="Graph of X" />);
    const svg = screen.getByRole("img", { name: "Graph of X: 3 accounts, 1 shared identifier and 3 payment links" });
    expect(svg.querySelectorAll("circle")).toHaveLength(3);
    expect(svg.querySelectorAll("rect")).toHaveLength(1);
    const lines = Array.from(svg.querySelectorAll("line"));
    expect(lines).toHaveLength(5);
    expect(lines.filter((l) => l.getAttribute("marker-end"))).toHaveLength(3); // only payments point
    expect(lines.filter((l) => l.getAttribute("stroke-dasharray"))).toHaveLength(2); // identifier links are dotted
    const fills = Array.from(svg.querySelectorAll("circle")).map((c) => c.getAttribute("fill"));
    expect(fills[0]).not.toBe(fills[1]); // a different score, a different shade
    expect(fills[2]).toBe("#ffffff"); // no score: unshaded
    expect(container.querySelectorAll("text")).toHaveLength(4); // few nodes: labelled
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  test("clicking a node, or picking it in the list, says what links to it; clicking again clears it", () => {
    const { container } = render(<CommunityGraph nodes={small} edges={smallEdges} />);
    fireEvent.click(container.querySelectorAll("g")[0]);
    expect(screen.getByRole("combobox", { name: "Member to inspect" })).toHaveValue("A1");
    expect(screen.getByText(/A1/, { selector: "p.font-medium" })).toHaveTextContent("A1, risk score 0.90");
    expect(screen.getByText("3 payments to A2, 500 in all.")).toBeInTheDocument();
    expect(screen.getByText("1 payment from A2, 100 in all.")).toBeInTheDocument();
    expect(screen.getByText("Shares device D-1 with 1 other account.")).toBeInTheDocument();
    fireEvent.click(container.querySelectorAll("g")[0]);
    expect(screen.getByRole("combobox", { name: "Member to inspect" })).toHaveValue("");

    fireEvent.change(screen.getByRole("combobox", { name: "Member to inspect" }), { target: { value: "A3" } });
    expect(screen.getByText("1 payment from A2, 100 in all.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Member to inspect" }), { target: { value: "" } });
    expect(screen.queryByText("1 payment from A2, 100 in all.")).not.toBeInTheDocument();
  });

  test("a member with no links says so", () => {
    render(<CommunityGraph nodes={[account("Z1", 0.2), account("Z2", 0.3)]} edges={[]} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Member to inspect" }), { target: { value: "Z1" } });
    expect(screen.getByText("Nothing else in this picture links to it.")).toBeInTheDocument();
  });

  test("past the cap it draws the highest-scoring accounts, keeps room for the identifiers, and says what it left out", () => {
    const many: GraphNode[] = [
      ...Array.from({ length: 320 }, (_, i) => account(`M${i}`, i / 320)),
      ...Array.from({ length: 4 }, (_, i) => identifier(`D${i}`)),
    ];
    const edges = [
      ...Array.from({ length: 320 }, (_, i) => pay(`M${i}`, `M${(i + 1) % 320}`)),
      ...many.slice(300, 320).map((n) => ({ source: n.id, target: "Device:D0", type: "USES_DEVICE", props: {} })),
    ];
    const { container } = render(<CommunityGraph nodes={many} edges={edges} memberCount={500} />);
    expect(GRAPH_CAP).toBe(300);
    expect(container.querySelectorAll("circle")).toHaveLength(296); // 300 less the 4 identifiers kept
    expect(container.querySelectorAll("rect")).toHaveLength(4);
    expect(container.querySelectorAll("text")).toHaveLength(0); // too many to label
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Drawing 300 of the 324 nodes the lab sent, the highest-scoring accounts first.");
    expect(note).toHaveTextContent("296 of the community's 500 members are drawn.");
    const ids = Array.from(container.querySelectorAll("title")).map((t) => t.textContent);
    expect(ids.some((t) => t?.startsWith("M319,"))).toBe(true); // the highest score is in
    expect(ids.some((t) => t?.startsWith("M0,"))).toBe(false); // the lowest is out
    expect(within(screen.getByRole("combobox", { name: "Member to inspect" })).getAllByRole("option")).toHaveLength(301);
  });

  test("a smaller cap can be asked for, and no nodes says so", () => {
    const { container, rerender } = render(<CommunityGraph nodes={Array.from({ length: 10 }, (_, i) => account(`M${i}`, i / 10))} edges={[]} cap={5} />);
    expect(container.querySelectorAll("circle")).toHaveLength(5);
    expect(screen.getByRole("note")).toHaveTextContent("Drawing 5 of the 10 nodes");
    rerender(<CommunityGraph nodes={[]} edges={[]} />);
    expect(screen.getByText("The lab drew no members for this community.")).toBeInTheDocument();
  });
});

// ---- SummaryBlock ------------------------------------------------------------------------------------------------------
const summary = (over: Partial<Summary>): Summary => ({
  key: "c1", text: "One.\n\nTwo.\n\nThree.", source: "api", model: "claude-x", evidence_hash: "abcdef0123456789", checks: {}, ...over,
});

describe("SummaryBlock", () => {
  test("an API summary names the model, shows its three parts as paragraphs and the checks it passed", () => {
    render(<SummaryBlock summary={summary({ checks: { numbers: true, members: true, verdict_words: true } })} />);
    expect(screen.getByText("Source: api")).toBeInTheDocument();
    expect(screen.getByText("Written by the Anthropic API, model claude-x.")).toBeInTheDocument();
    expect(["One.", "Two.", "Three."].every((t) => screen.getByText(t).tagName === "P")).toBe(true);
    expect(screen.getByText(/Every number is in the evidence/).closest("li")).toHaveTextContent("passed");
    expect(screen.getByText("abcdef012345")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("an agent summary says an agent wrote it", () => {
    render(<SummaryBlock summary={summary({ source: "agent", model: null })} />);
    expect(screen.getByText("Source: agent")).toBeInTheDocument();
    expect(screen.getByText("Written by a connected agent through describe_alert.")).toBeInTheDocument();
  });

  test("a template says no model wrote it", () => {
    const s = summary({ source: "template", model: null });
    render(<SummaryBlock summary={s} />);
    expect(screen.getByText("Source: template")).toBeInTheDocument();
    expect(screen.getByText("Built from the evidence by a template. No model wrote this text.")).toBeInTheDocument();
    expect(wasReplaced(s)).toBe(false);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("a template that replaced a failed text says so and why; a failed check on a written text is an error", () => {
    const replaced = summary({ source: "template", model: null, checks: { numbers: false, odd_check: true }, failed_reason: "14 is not in the evidence." });
    expect(wasReplaced(replaced)).toBe(true);
    expect(wasReplaced(summary({ source: "template", checks: {}, failed_reason: "x" }))).toBe(true);
    const { rerender } = render(<SummaryBlock summary={replaced} />);
    expect(screen.getByRole("alert")).toHaveTextContent("A model's text was replaced by the template");
    expect(screen.getByRole("alert")).toHaveTextContent("14 is not in the evidence.");
    expect(screen.getByText(/Odd check/)).toBeInTheDocument(); // an unknown check shows as words

    rerender(<SummaryBlock summary={summary({ checks: { members: false } })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("A check failed on this text");
    expect(screen.queryByText(/replaced/)).not.toBeInTheDocument();
  });

  test("no summary says why", () => {
    render(<SummaryBlock summary={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("No summary for this community");
  });
});

// ---- CodeDrawer --------------------------------------------------------------------------------------------------------
describe("CodeDrawer", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>Open it</button>
        <CodeDrawer open={open} onClose={() => setOpen(false)} title="The code" description="About it" path="a/b.py"
          notice={<p>No source here.</p>} tabs={[{ id: "one", label: "One", text: "first" }, { id: "two", label: "Two", text: "second" }]} />
      </>
    );
  }

  test("it renders nothing until opened, then shows the path, the notice and the first tab", () => {
    render(<Harness />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open it" }));
    const dialog = screen.getByRole("dialog", { name: "The code" });
    expect(dialog).toHaveTextContent("About it");
    expect(dialog).toHaveTextContent("a/b.py");
    expect(dialog).toHaveTextContent("No source here.");
    expect(dialog.querySelector("pre")).toHaveTextContent("first");
    expect(within(dialog).getByRole("button", { name: "Close" })).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Two" }));
    expect(dialog.querySelector("pre")).toHaveTextContent("second");
  });

  test("Escape, Close and the backdrop leave it, and focus goes back to the button; it opens on the first tab again", () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open it" });
    opener.focus();
    fireEvent.click(opener);
    fireEvent.click(screen.getByRole("tab", { name: "Two" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    fireEvent.click(opener);
    expect(screen.getByRole("tab", { name: "One" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("a tab that names its language says it above the text and sets it on the code element, and a tab without one does not", () => {
    render(
      <CodeDrawer open onClose={() => undefined} title="The code"
        tabs={[{ id: "q", label: "factors/x.cypher", text: "MATCH (n) RETURN n", language: "cypher" }, { id: "plain", label: "Plain", text: "words" }]} />);
    const dialog = screen.getByRole("dialog", { name: "The code" });
    expect(within(dialog).getByText("Cypher")).toBeInTheDocument();
    expect(dialog.querySelector("pre code")).toHaveClass("language-cypher");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Plain" }));
    expect(within(dialog).queryByText("Cypher")).not.toBeInTheDocument();
    expect(dialog.querySelector("pre code")!.className).toBe("");
  });

  test("Tab stays inside, and Copy puts the shown text on the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Open it" }));
    const dialog = screen.getByRole("dialog");
    const text = dialog.querySelector("pre") as HTMLElement; // the last thing a Tab reaches
    text.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(within(dialog).getByRole("button", { name: "Close" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(text).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("first"));
    expect(await within(dialog).findByText("Copied to the clipboard.")).toBeInTheDocument();
  });
});

// ---- the hooks ---------------------------------------------------------------------------------------------------------
describe("useLoad", () => {
  function Probe({ id, load }: { id: string | null; load: (id: string) => Promise<string> }) {
    const { data, error, loading, reload } = useLoad(id ? () => load(id) : null, `k|${id}`);
    return (
      <div>
        <p data-testid="out">{loading ? "loading" : error ? `error: ${error}` : data ?? "idle"}</p>
        <button onClick={reload}>again</button>
      </div>
    );
  }

  test("it asks once per key, forgets the old answer at once, and asks again on reload", async () => {
    const load = vi.fn(async (id: string) => `data ${id}`);
    const { rerender } = render(<Probe id="a" load={load} />);
    expect(screen.getByTestId("out")).toHaveTextContent("loading");
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("data a"));
    rerender(<Probe id="b" load={load} />);
    expect(screen.getByTestId("out")).toHaveTextContent("loading"); // never run a's data under b
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("data b"));
    fireEvent.click(screen.getByRole("button", { name: "again" }));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(3));
    expect(screen.getByTestId("out")).toHaveTextContent("data b");
  });

  test("an error is the message, and nothing is asked without a key", async () => {
    const load = vi.fn(async () => { throw new Error("it broke"); });
    const { rerender } = render(<Probe id="a" load={load} />);
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("error: it broke"));
    load.mockClear();
    rerender(<Probe id={null} load={load} />);
    expect(screen.getByTestId("out")).toHaveTextContent("idle");
    expect(load).not.toHaveBeenCalled();
  });

  test("an answer that arrives after the screen moved on is dropped", async () => {
    let release: (v: string) => void = () => {};
    const load = vi.fn((id: string) => id === "slow" ? new Promise<string>((r) => { release = r; }) : Promise.resolve(`data ${id}`));
    const { rerender } = render(<Probe id="slow" load={load} />);
    rerender(<Probe id="fast" load={load} />);
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("data fast"));
    await act(async () => { release("data slow"); });
    expect(screen.getByTestId("out")).toHaveTextContent("data fast");
  });
});

describe("useStoredText", () => {
  function Name() {
    const [name, setName] = useStoredText("test.name");
    return <input aria-label="name" value={name} onChange={(e) => setName(e.target.value)} />;
  }

  test("it keeps the text in localStorage and reads it back", () => {
    window.localStorage.setItem("test.name", "Ada");
    const first = render(<Name />);
    expect(screen.getByLabelText("name")).toHaveValue("Ada");
    fireEvent.change(screen.getByLabelText("name"), { target: { value: "Grace" } });
    expect(window.localStorage.getItem("test.name")).toBe("Grace");
    first.unmount();
    render(<Name />);
    expect(screen.getByLabelText("name")).toHaveValue("Grace");
  });

  test("blocked storage still works for the page", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<Name />);
    expect(screen.getByLabelText("name")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("name"), { target: { value: "Ada" } });
    expect(screen.getByLabelText("name")).toHaveValue("Ada");
  });
});
