import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { mockRunIds, resetMockRuns } from "../api/mock";
import type { PushResult, Review } from "../api/types";
import { summarySentence } from "./Review";

beforeEach(() => { setMock(true); window.localStorage.clear(); resetMockRuns(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); });

const DONE = mockRunIds.done;
const PARTIAL = mockRunIds.partialPush;
const SENTENCE = "This push adds 12 alerts and 7 communities, updates scores on 96 accounts, and deletes nothing.";

const mount = (id: string = DONE) => render(<MemoryRouter initialEntries={[`/review?run=${id}`]}><App /></MemoryRouter>);
const loaded = () => screen.findByText(SENTENCE);
const pushButton = () => screen.getByRole("button", { name: "Push to the master graph" });
const openDialog = () => { fireEvent.click(pushButton()); return screen.getByRole("alertdialog"); };
const confirmPush = (dialog: HTMLElement) => fireEvent.click(within(dialog).getByRole("button", { name: "Confirm and push" }));
const stat = (label: string) => screen.getByText(label).closest("dl")!;
const alertWith = (text: string) => screen.getByText(text).closest('[role="alert"]') as HTMLElement;

test("with no run it says so and points to Slice, and asks for nothing", () => {
  const review = vi.spyOn(api, "review");
  render(<MemoryRouter initialEntries={["/review"]}><App /></MemoryRouter>);
  expect(screen.getByRole("status")).toHaveTextContent("No run yet");
  expect(within(screen.getByRole("status")).getByRole("link", { name: "Slice" })).toHaveAttribute("href", "/slice");
  expect(review).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Push to the master graph" })).not.toBeInTheDocument();
});

test("it opens with one sentence that uses the delta's numbers, before the delta, the plan or anything else", async () => {
  const review = vi.spyOn(api, "review");
  mount();
  await loaded();
  expect(review).toHaveBeenCalledWith(DONE);

  const summary = screen.getByRole("region", { name: "What this push will do" });
  expect(summary).toHaveTextContent(SENTENCE);
  expect(summary).toHaveTextContent(`Run ${DONE}`);
  expect(summary).toHaveTextContent("Master graph: http://localhost:17474");
  for (const later of ["What changes", "The plan", "Push to the master graph"]) {
    const region = screen.getByRole("region", { name: later });
    expect(summary.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
});

test("the sentence says one alert, one community, one account, and zero is still a number", () => {
  const delta = { new_alerts: 1, new_communities: 1, accounts_scored: 1, score_changes: {}, missing_remote_keys: 0, sampled: 0, vectors: 0, dispositions: 0, bytes_estimate: 0 };
  expect(summarySentence(delta)).toBe("This push adds 1 alert and 1 community, updates scores on 1 account, and deletes nothing.");
  expect(summarySentence({ ...delta, new_alerts: 0, new_communities: 1200, accounts_scored: 0 }))
    .toBe("This push adds 0 alerts and 1,200 communities, updates scores on 0 accounts, and deletes nothing.");
});

test("the delta shows as numbers, and the score changes as bars with their counts, in order", async () => {
  mount();
  await loaded();
  const expected: [string, string][] = [
    ["New alerts", "12"], ["New communities", "7"], ["Accounts scored", "96"], ["Vectors", "412"], ["Dispositions", "4"],
    ["Data to send", "2.5 MB"],
  ];
  for (const [label, value] of expected) expect(stat(label)).toHaveTextContent(value);

  const table = screen.getByRole("table", { name: "Accounts by how far their score moved" });
  const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
  expect(rows).toEqual(["0.0-0.141", "0.1-0.228", "0.2-0.315", "0.3-0.48", "0.4-0.53", "0.5-1.01"]);
  expect(within(table).getAllByRole("row")[1].querySelector("[style]")).toHaveStyle({ width: "100%" }); // the biggest bucket is full width
});

test("a sample that found keys missing on the master graph is a warning, above the delta", async () => {
  mount(PARTIAL);
  await loaded();
  const warning = alertWith("Some accounts are not on the master graph");
  expect(warning).toHaveTextContent("3 of the 50 accounts the lab sampled were not found there");
  expect(warning).toHaveTextContent("may create those accounts");
  const summary = screen.getByRole("region", { name: "What this push will do" });
  expect(summary.compareDocumentPosition(warning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(warning.compareDocumentPosition(screen.getByRole("region", { name: "What changes" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("with nothing missing there is no warning, and it says all the sampled keys were found", async () => {
  mount();
  await loaded();
  expect(screen.queryByText("Some accounts are not on the master graph")).not.toBeInTheDocument();
  expect(screen.getByText("All 50 sampled accounts were found on the master graph.")).toBeInTheDocument();
});

test("the plan is grouped by kind, nodes before relationships, though the lab listed them mixed", async () => {
  mount();
  await loaded();
  const plan = screen.getByRole("region", { name: "The plan" });
  expect(within(plan).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Nodes", "Relationships"]);
  expect(within(plan).getAllByRole("table").map((t) => t.querySelector("caption")?.textContent))
    .toEqual(["The plan: nodes", "The plan: relationships"]);

  const nodes = screen.getByRole("table", { name: "The plan: nodes" });
  const entities = (t: HTMLElement) => within(t).getAllByRole("row").slice(1).map((r) => (r as HTMLTableRowElement).cells[0].textContent);
  expect(entities(nodes)).toEqual(["Alert", "Account", "RiskCommunity"]);
  expect(entities(screen.getByRole("table", { name: "The plan: relationships" }))).toEqual(["HAS_ALERT", "IN_COMMUNITY"]);
  expect(within(nodes).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Entity", "Total", "Created", "Updated", "Skipped", "Batches"]);
  expect(within(within(nodes).getAllByRole("row")[2]).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Account", "96", "0", "96", "0", "1"]);
  expect(plan).toHaveTextContent("not a comparison with the master graph");
});

test("the dry run as Relate printed it is in a block that starts closed", async () => {
  mount();
  await loaded();
  const details = screen.getByText("The dry run, as Relate printed it").closest("details")!;
  expect(details).not.toHaveAttribute("open");
  const pre = within(details).getByLabelText("Output of relate push --dry-run");
  expect(pre.tagName).toBe("PRE");
  expect(pre.className).toContain("font-mono");
  expect(pre.textContent).toContain("node          RiskCommunity    7 rows  1 batch");
});

test("nothing is written until the dialog is accepted: the button only asks, and Cancel and Escape say no", async () => {
  const push = vi.spyOn(api, "push");
  mount();
  await loaded();
  expect(push).not.toHaveBeenCalled();
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

  let dialog = openDialog();
  expect(push).not.toHaveBeenCalled();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus(); // focus starts on the safe answer
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

  dialog = openDialog();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(push).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();

  dialog = openDialog();
  confirmPush(dialog);
  await screen.findByText("Pushed to the master graph");
  expect(push).toHaveBeenCalledTimes(1);
  expect(push).toHaveBeenCalledWith(DONE, true);
});

test("the dialog says what will be written, where, and that nothing is deleted", async () => {
  mount();
  await loaded();
  const dialog = openDialog();
  expect(within(dialog).getByRole("heading", { name: "Push to the master graph?" })).toBeInTheDocument();
  expect(dialog).toHaveTextContent("Master graph: http://localhost:17474");
  expect(dialog).toHaveTextContent(SENTENCE);
  expect(dialog).toHaveTextContent("412 vectors and 4 reviewer decisions, about 2.5 MB in all");
  expect(dialog).toHaveTextContent("Nothing is deleted on the master graph");
  expect(dialog).toHaveTextContent(`run id ${DONE}`);
  expect(dialog).toHaveTextContent("uniqueness constraints on its own labels only (Alert, RiskCommunity, LabRun)");
  expect(dialog).toHaveTextContent("A LabRun node is written last");
  expect(dialog).toHaveAccessibleName("Push to the master graph?");
});

test("a push that lands shows the counts and a way to Results, and the button is gone", async () => {
  mount();
  await loaded();
  confirmPush(openDialog());

  const done = (await screen.findByText("Pushed to the master graph")).closest('[role="status"]') as HTMLElement;
  expect(done).toHaveTextContent("Pushed 5 entities. LabRun written last.");
  expect(done).toHaveTextContent("5 entities landed: 62 created, 96 updated. Nothing was deleted.");
  expect(within(done).getByRole("link", { name: "Go to Results" })).toHaveAttribute("href", `/results?run=${DONE}`);
  const landed = screen.getByRole("table", { name: "What landed" });
  expect(within(landed).getAllByText("Yes")).toHaveLength(5);
  expect(screen.queryByRole("button", { name: "Push to the master graph" })).not.toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

test("a partial push names what did not land and why, and Resume push goes through the same confirm", async () => {
  const push = vi.spyOn(api, "push");
  mount(PARTIAL);
  await loaded();
  confirmPush(openDialog());

  const failed = (await screen.findByText("The push stopped part way. Nothing was deleted.")).closest('[role="alert"]') as HTMLElement;
  expect(failed).toHaveTextContent("The push stopped at RiskCommunity. 2 of 5 entities landed.");
  expect(failed).toHaveTextContent("RiskCommunity (node): Batch 1 (rows 1 to 7, MERGE on :RiskCommunity) failed: the Query API answered 503.");
  expect(failed).toHaveTextContent("HAS_ALERT (relationship): Not sent: the push stopped at an earlier batch.");
  expect(failed).toHaveTextContent("IN_COMMUNITY (relationship)");
  expect(failed).not.toHaveTextContent("Alert (node):"); // the one that landed is not listed as failed
  const landed = screen.getByRole("table", { name: "What landed so far" });
  expect(within(landed).getAllByText("No")).toHaveLength(3);
  expect(screen.queryByRole("button", { name: "Push to the master graph" })).not.toBeInTheDocument();
  expect(push).toHaveBeenCalledTimes(1);

  // Resume push asks first, and says what it will send
  fireEvent.click(screen.getByRole("button", { name: "Resume push" }));
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByRole("heading", { name: "Resume the push?" })).toBeInTheDocument();
  expect(dialog).toHaveTextContent("RiskCommunity (node)");
  expect(dialog).toHaveTextContent("Nothing is deleted on the master graph");
  expect(push).toHaveBeenCalledTimes(1);
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm and resume push" }));

  expect(await screen.findByText("Pushed. The push resumed and finished.")).toBeInTheDocument();
  expect(screen.getByText(/Resumed: pushed the 3 entities that had not landed/)).toBeInTheDocument();
  expect(push).toHaveBeenCalledTimes(2);
  expect(push).toHaveBeenNthCalledWith(2, PARTIAL, true);
  expect(screen.queryByRole("button", { name: "Resume push" })).not.toBeInTheDocument();
});

test("a second click cannot send a second push while one is on its way", async () => {
  let release: (r: PushResult) => void = () => {};
  const push = vi.spyOn(api, "push").mockImplementation(() => new Promise((resolve) => { release = resolve; }));
  mount();
  await loaded();
  const dialog = openDialog();
  const confirm = within(dialog).getByRole("button", { name: "Confirm and push" });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(push).toHaveBeenCalledTimes(1);

  // the dialog is gone, the button is off and says so, and clicking it opens nothing
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(pushButton()).toBeDisabled();
  expect(pushButton()).toHaveAttribute("aria-busy", "true");
  fireEvent.click(pushButton());
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(screen.getByText(/Sending to the master graph/)).toBeInTheDocument();
  expect(push).toHaveBeenCalledTimes(1);

  await act(async () => { release({ run_id: DONE, pushed: true, resumed: false, entities: [], message: "Pushed 0 entities." }); });
  expect(await screen.findByText("Pushed to the master graph")).toBeInTheDocument();
  expect(push).toHaveBeenCalledTimes(1);
});

test("a push the lab refuses or cannot make shows the reason, and the button is there to try again through the dialog", async () => {
  const push = vi.spyOn(api, "push").mockRejectedValue(new Error("The lab could not reach the master graph."));
  mount();
  await loaded();
  confirmPush(openDialog());
  const failed = (await screen.findByText("The push request failed")).closest('[role="alert"]') as HTMLElement;
  expect(failed).toHaveTextContent("The lab could not reach the master graph.");
  expect(failed).toHaveTextContent("Nothing is deleted on the master graph.");
  expect(pushButton()).toBeEnabled();
  expect(push).toHaveBeenCalledTimes(1);
  fireEvent.click(pushButton());
  expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  expect(push).toHaveBeenCalledTimes(1);
});

test("a review that does not load shows why, with Try again, and no way to push", async () => {
  const review = vi.spyOn(api, "review").mockRejectedValueOnce(new Error("The run has not reached its review step."));
  mount();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("The review did not load");
  expect(alert).toHaveTextContent("The run has not reached its review step.");
  expect(screen.queryByRole("button", { name: "Push to the master graph" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await loaded();
  expect(review).toHaveBeenCalledTimes(2);
  expect(pushButton()).toBeEnabled();
});

test("when the lab cannot say which graph is connected, the screen says so rather than leaving it out", async () => {
  vi.spyOn(api, "connection").mockRejectedValue(new Error("connection check timed out"));
  mount();
  await loaded();
  await waitFor(() => expect(screen.getByRole("region", { name: "What this push will do" }))
    .toHaveTextContent("The lab could not say which graph is connected: connection check timed out"));
});

test("a plan with skipped rows says what skipped means; an empty plan says there is nothing to send", async () => {
  const base = await api.review(DONE);
  const withSkips: Review = { ...base, plan: base.plan.map((e) => (e.entity === "Account" ? { ...e, skipped: 5 } : e)) };
  const spy = vi.spyOn(api, "review").mockResolvedValue(withSkips);
  const first = mount();
  await loaded();
  expect(screen.getByText(/Skipped is what the push leaves out/)).toBeInTheDocument();
  first.unmount();

  spy.mockResolvedValue({ ...base, plan: [] });
  mount();
  await loaded();
  expect(screen.getByText("The dry run lists nothing to send.")).toBeInTheDocument();
});

test("the mock refuses a push without confirm, as apply_push does", async () => {
  await expect(api.push(DONE, false)).rejects.toThrow(/confirm is not set/);
});
