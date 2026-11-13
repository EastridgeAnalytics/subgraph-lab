import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { mockData, mockRunIds, resetMockRuns } from "../api/mock";
import type { RunManifest, Step, StepStatus } from "../api/types";
import { defaultRecipe, STORAGE_KEY } from "../state/run";
import { formatDuration, STEP_ORDER } from "./Run";

beforeEach(() => { setMock(true); window.localStorage.clear(); resetMockRuns(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); vi.useRealTimers(); });

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}
const mount = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /><Where /></MemoryRouter>);
const where = () => screen.getByTestId("where").textContent;
const steps = () => within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");

const step = (name: string, status: StepStatus, extra: Partial<Step> = {}): Step => ({ name, status, counts: {}, ...extra });
const manifest = (status: StepStatus, list: Step[] = [], extra: Partial<RunManifest> = {}): RunManifest =>
  ({ ...mockData.run, status, steps: list, routes: {}, fallbacks: [], ...extra });

const TITLES = [
  "Pull the slice from the master graph", "Build the search indexes", "First pass: embeddings and summaries", "Pick the slice",
  "Run the models", "Score the accounts", "Find the communities", "Write the community summaries",
  "Stage the results in the push store", "Prepare the review: dry run and delta", "Push to the master graph",
];

test("with no run it says so and points to Slice", () => {
  const run = vi.spyOn(api, "run");
  mount("/run");
  expect(screen.getByRole("heading", { level: 1, name: "Run" })).toBeInTheDocument();
  const notice = screen.getByRole("status");
  expect(notice).toHaveTextContent("No run yet");
  expect(within(notice).getByRole("link", { name: "Slice" })).toHaveAttribute("href", "/slice");
  expect(run).not.toHaveBeenCalled();
});

test("an address that is not a plain run id is refused before it reaches the API", () => {
  const run = vi.spyOn(api, "run");
  mount("/run?run=..%2Fpush");
  expect(screen.getByRole("alert")).toHaveTextContent("That is not a run id");
  expect(run).not.toHaveBeenCalled();
});

test("one row per step, in the lab's order, with plain names, counts and the time each took", async () => {
  mount(`/run?run=${mockRunIds.partialPush}`);
  await screen.findByRole("list", { name: "Steps" });
  const rows = steps();
  expect(STEP_ORDER.length).toBe(11);
  expect(rows).toHaveLength(11);
  rows.forEach((row, i) => expect(row).toHaveTextContent(TITLES[i]));

  // pull: counts formatted with separators, the time it took, and the route it took
  expect(rows[0]).toHaveTextContent("Nodes 412");
  expect(rows[0]).toHaveTextContent("Relationships 1,630");
  expect(rows[0]).toHaveTextContent("2.5 s");
  expect(rows[0]).toHaveTextContent("Route Relate");
  expect(rows[2]).toHaveTextContent("First pass: embeddings and summaries");
  expect(rows[2]).toHaveTextContent("Embeddings 412");

  // a bar per step: full when done, empty when waiting
  expect(within(rows[0]).getByRole("progressbar", { name: TITLES[0] })).toHaveAttribute("aria-valuenow", "100");
  expect(within(rows[10]).getByRole("progressbar", { name: TITLES[10] })).toHaveAttribute("aria-valuenow", "0");
  expect(rows[10]).toHaveTextContent("Nothing is sent until you confirm on Review and push.");
  expect(screen.getByRole("status")).toHaveTextContent("10 of 11 steps done");
});

test("a step the manifest does not list still gets its row, and one the lab added is not hidden", async () => {
  vi.spyOn(api, "run").mockResolvedValue(manifest("done", [step("pull", "done"), step("warmup", "done", { counts: { cache_hits: 3 } })]));
  mount("/run?run=run-x");
  await screen.findByRole("list", { name: "Steps" });
  const rows = steps();
  expect(rows).toHaveLength(12);
  expect(rows[1]).toHaveTextContent("Build the search indexes");
  expect(rows[1]).toHaveTextContent("Not run"); // the run is done and this step never ran
  expect(rows[11]).toHaveTextContent("Warmup");
  expect(rows[11]).toHaveTextContent("Cache hits 3");
});

test("it reads the run from the run context, and ?run= in the address wins over it", async () => {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe: defaultRecipe(), runId: mockRunIds.fallback, runRecipe: null }));
  const run = vi.spyOn(api, "run");
  const first = mount("/run");
  await screen.findByRole("list", { name: "Steps" });
  expect(run).toHaveBeenCalledWith(mockRunIds.fallback);
  first.unmount();

  run.mockClear();
  mount(`/run?run=${mockRunIds.partialPush}`);
  await screen.findByRole("list", { name: "Steps" });
  expect(run).toHaveBeenCalledWith(mockRunIds.partialPush);
  expect(run).not.toHaveBeenCalledWith(mockRunIds.fallback);
});

test("a running step shows a bar with no fraction, its live counts and the time so far", async () => {
  mount(`/run?run=${mockRunIds.advancing}`);
  const first = (await screen.findAllByRole("listitem"))[0];
  expect(first).toHaveTextContent("Running");
  expect(within(first).getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  expect(first).toHaveTextContent("Nodes 206"); // the mock shows half of pull's counts while it runs
});

test("a failed step says what failed and offers Resume, and Resume calls the API and picks the run back up", async () => {
  const resume = vi.spyOn(api, "resume");
  mount(`/run?run=${mockRunIds.failed}`);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Run the models failed");
  expect(alert).toHaveTextContent("The model container ran out of memory while training TGN.");
  expect(steps()[4]).toHaveTextContent("Failed");
  expect(steps()[3]).toHaveTextContent("Done");
  expect(resume).not.toHaveBeenCalled();

  fireEvent.click(within(alert).getByRole("button", { name: "Resume" }));
  await waitFor(() => expect(resume).toHaveBeenCalledWith(mockRunIds.failed));
  expect(resume).toHaveBeenCalledTimes(1);
  // the screen asks again at once: the failure is gone and models is running
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(steps()[4]).toHaveTextContent("Running");
});

test("Resume cannot be sent twice while it is on its way", async () => {
  let release: () => void = () => {};
  const resume = vi.spyOn(api, "resume").mockImplementation(() => new Promise((resolve) => {
    release = () => resolve({ run_id: mockRunIds.failed });
  }));
  mount(`/run?run=${mockRunIds.failed}`);
  const button = await screen.findByRole("button", { name: "Resume" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(button).toBeDisabled();
  expect(resume).toHaveBeenCalledTimes(1);
  await act(async () => { release(); });
});

test("a resume the lab refuses is shown, and Resume stays available", async () => {
  vi.spyOn(api, "resume").mockRejectedValue(new Error("The lab is busy with another run."));
  mount(`/run?run=${mockRunIds.failed}`);
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  expect(await screen.findByText("The lab is busy with another run.")).toBeInTheDocument();
  expect(screen.getByText("The run did not resume")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled();
});

test("a push that stopped is resumed on Review and push, never from here", async () => {
  const resume = vi.spyOn(api, "resume");
  vi.spyOn(api, "run").mockResolvedValue(manifest("failed", [
    ...STEP_ORDER.slice(0, 10).map((n) => step(n, "done")),
    step("push", "failed", { note: "Batch 2 of RiskCommunity failed: the Query API answered 503." }),
  ]));
  mount("/run?run=run-x");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Batch 2 of RiskCommunity failed");
  expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  fireEvent.click(within(alert).getByRole("button", { name: "Open Review and push" }));
  expect(where()).toBe("/review?run=run-x");
  await screen.findByText("The review did not load"); // the mock has no review for this made-up run; let Review settle
  expect(resume).not.toHaveBeenCalled();
});

test("a fallback shows its step, its route and its reason, never hidden", async () => {
  mount(`/run?run=${mockRunIds.fallback}`);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Pull the slice from the master graph: took the Bolt fallback route");
  expect(alert).toHaveTextContent("The Query API could not be reached: connection refused at localhost:17474.");
  expect(steps()[0]).toHaveTextContent("Route Bolt (fallback)");
});

test("a skipped step shows its note", async () => {
  vi.spyOn(api, "run").mockResolvedValue(manifest("done", [
    step("pull", "done"), step("indexes", "done"),
    step("first_pass", "skipped", { note: "Search is off in this recipe, so there is nothing to embed." }),
  ]));
  mount("/run?run=run-x");
  await screen.findByRole("list", { name: "Steps" });
  expect(steps()[2]).toHaveTextContent("Skipped");
  expect(steps()[2]).toHaveTextContent("Search is off in this recipe, so there is nothing to embed.");
});

test("it asks about once a second while the run is going and stops when it is done", async () => {
  vi.useFakeTimers();
  const going = manifest("running", [step("pull", "running", { started_at: new Date().toISOString() })]);
  const run = vi.spyOn(api, "run")
    .mockResolvedValueOnce(going).mockResolvedValueOnce(going).mockResolvedValue(manifest("done", [step("pull", "done")]));
  mount("/run?run=run-x");
  const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  await advance(0);
  expect(run).toHaveBeenCalledTimes(1);
  await advance(999);
  expect(run).toHaveBeenCalledTimes(1);
  await advance(1);
  expect(run).toHaveBeenCalledTimes(2);
  await advance(1000);
  expect(run).toHaveBeenCalledTimes(3); // this one said done
  expect(screen.getByRole("button", { name: "See the results" })).toBeInTheDocument();
  await advance(10_000);
  expect(run).toHaveBeenCalledTimes(3);
});

test("it also stops when the run failed, and when the screen is left", async () => {
  vi.useFakeTimers();
  const run = vi.spyOn(api, "run").mockResolvedValue(manifest("failed", [step("pull", "failed", { note: "No route to the master graph." })]));
  const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  const failed = mount("/run?run=run-x");
  await advance(5000);
  expect(run).toHaveBeenCalledTimes(1);
  failed.unmount();

  run.mockReset().mockResolvedValue(manifest("running", [step("pull", "running")]));
  const going = mount("/run?run=run-y");
  await advance(2000);
  const seen = run.mock.calls.length;
  expect(seen).toBeGreaterThanOrEqual(2);
  going.unmount();
  await advance(5000);
  expect(run).toHaveBeenCalledTimes(seen);
});

test("an answer that does not come shows why, stops asking, and Try again starts it over", async () => {
  vi.useFakeTimers();
  const run = vi.spyOn(api, "run").mockRejectedValueOnce(new Error("The lab did not answer in time."))
    .mockResolvedValue(manifest("done", [step("pull", "done")]));
  mount("/run?run=run-x");
  const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  await advance(0);
  expect(screen.getByRole("alert")).toHaveTextContent("The lab did not answer in time.");
  await advance(5000);
  expect(run).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await advance(0);
  expect(run).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(steps()).toHaveLength(11);
});

test("a finished run offers Results first and Review second, and keeps following the run it was opened with", async () => {
  mount(`/run?run=${mockRunIds.partialPush}`);
  const results = await screen.findByRole("button", { name: "See the results" });
  const review = screen.getByRole("button", { name: "Review and push" });
  expect(results.className).toContain("bg-indigo-600"); // the primary one
  expect(review.className).not.toContain("bg-indigo-600");
  fireEvent.click(results);
  expect(where()).toBe(`/results?run=${mockRunIds.partialPush}`);
});

test("from the run context, the buttons go to plain /results and /review", async () => {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe: defaultRecipe(), runId: mockRunIds.fallback, runRecipe: null }));
  mount("/run");
  fireEvent.click(await screen.findByRole("button", { name: "Review and push" }));
  expect(where()).toBe("/review");
  await screen.findByText("The review did not load"); // the mock has no review for this run; let Review settle
});

test("a run that is still going has no buttons to leave yet", async () => {
  mount(`/run?run=${mockRunIds.advancing}`);
  await screen.findByRole("list", { name: "Steps" });
  expect(screen.queryByRole("button", { name: "See the results" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review and push" })).not.toBeInTheDocument();
});

test("durations read in plain units", () => {
  expect(formatDuration(40)).toBe("under 0.1 s");
  expect(formatDuration(2500)).toBe("2.5 s");
  expect(formatDuration(12_400)).toBe("12 s");
  expect(formatDuration(125_000)).toBe("2 min 05 s");
  expect(formatDuration(3_780_000)).toBe("1 h 03 min");
});
