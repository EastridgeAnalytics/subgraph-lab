import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { MOCK_TIMELINE_DAYS, mockTimeline } from "../api/mock";
import type { Timeline as TimelineData } from "../api/types";
import { defaultRecipe, STORAGE_KEY } from "../state/run";
import { PAUSE_MS } from "./Timeline";

beforeEach(() => { setMock(true); window.localStorage.clear(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); vi.useRealTimers(); });

const RUN = "run-x";
const LAST = MOCK_TIMELINE_DAYS - 1; // the slider's last place
// The day the slider is on at place i: the mock starts on 2026-09-01.
const dayAt = (i: number) => new Date(Date.parse("2026-09-01T00:00:00Z") + i * 86_400_000).toISOString().slice(0, 10);

const mount = (path = `/timeline?run=${RUN}`) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
const slider = () => screen.getByRole("slider", { name: "As of" });
const ready = () => screen.findByRole("slider", { name: "As of" });
const move = (to: number) => fireEvent.change(slider(), { target: { value: String(to) } });
const release = () => fireEvent.pointerUp(slider());
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

const totalsBox = () => within(screen.getByRole("region", { name: "Totals up to the as-of day" }));
const stat = (label: string) => totalsBox().getByText(label).closest("dl")!;
const statValue = (label: string) => stat(label).querySelector("dd")?.textContent;

const INT = (n: number) => n.toLocaleString("en-US");
const MONEY = (n: number) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
/** What the totals should read, worked out from the mock's own points and nothing the screen computed. */
function expected(asOf?: string) {
  const points = mockTimeline(asOf).points;
  return {
    payments: INT(points.reduce((n, p) => n + p.payments, 0)),
    amount: MONEY(Math.round(points.reduce((n, p) => n + p.amount, 0) * 100) / 100),
    accounts: INT(Math.max(...points.map((p) => p.accounts))),
    withPayments: points.length,
  };
}

test("with no run it says so and points to Slice, and asks the lab nothing", () => {
  const timeline = vi.spyOn(api, "timeline");
  mount("/timeline");
  expect(screen.getByRole("heading", { level: 1, name: "Timeline" })).toBeInTheDocument();
  const notice = screen.getByRole("status");
  expect(notice).toHaveTextContent("No run yet");
  expect(within(notice).getByRole("link", { name: "Slice" })).toHaveAttribute("href", "/slice");
  expect(timeline).not.toHaveBeenCalled();
});

test("an address that is not a plain run id is refused before it reaches the API", () => {
  const timeline = vi.spyOn(api, "timeline");
  mount("/timeline?run=..%2Fpush");
  expect(screen.getByRole("alert")).toHaveTextContent("That is not a run id");
  expect(timeline).not.toHaveBeenCalled();
});

test("it reads the run from the run context, and ?run= in the address wins over it", async () => {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe: defaultRecipe(), runId: "run-from-context", runRecipe: null }));
  const timeline = vi.spyOn(api, "timeline");
  const first = mount("/timeline");
  await ready();
  expect(timeline).toHaveBeenCalledWith("run-from-context");
  first.unmount();

  timeline.mockClear();
  mount("/timeline?run=run-from-address");
  await ready();
  expect(timeline).toHaveBeenCalledWith("run-from-address");
  expect(timeline).not.toHaveBeenCalledWith("run-from-context");
});

test("it loads the whole range once and draws it: a slider over the days, a bar for every day with payments", async () => {
  const timeline = vi.spyOn(api, "timeline");
  mount();
  const s = await ready();
  expect(timeline).toHaveBeenCalledTimes(1);
  expect(timeline).toHaveBeenCalledWith(RUN); // no as_of: the whole range

  expect(s).toHaveAttribute("type", "range");
  expect(s).toHaveAttribute("min", "0");
  expect(s).toHaveAttribute("max", String(LAST));
  expect(s).toHaveValue(String(LAST));
  expect(s).toHaveAttribute("aria-valuetext", expect.stringContaining("Oct 15, 2026"));
  expect(screen.getByText("Sep 1, 2026")).toBeInTheDocument(); // the two ends of the slider
  expect(screen.getAllByText("Oct 15, 2026").length).toBeGreaterThan(0);

  const chart = screen.getByRole("img", { name: "Payments per day, Sep 1, 2026 to Oct 15, 2026" });
  const bars = [...chart.querySelectorAll("[data-bar]")];
  expect(bars).toHaveLength(MOCK_TIMELINE_DAYS - 1); // the mock has one day with no payments
  expect(chart.querySelector('[data-bar="2026-09-07"]')).toBeNull();
  expect(bars.some((b) => b.hasAttribute("data-muted"))).toBe(false); // the slider is at the end: nothing is after it
  expect(within(chart).getByText("Sep 1")).toBeInTheDocument(); // dates on the axis
  expect(within(chart).getByText("Oct 15")).toBeInTheDocument();
  expect(within(chart).getByText("60")).toBeInTheDocument(); // and numbers
});

test("the totals at the end are the whole range's: days, payments, amount and the most accounts in a day", async () => {
  mount();
  await ready();
  const all = expected();
  expect(statValue("Days")).toBe(String(MOCK_TIMELINE_DAYS));
  expect(stat("Days")).toHaveTextContent(`${all.withPayments} with payments`);
  expect(statValue("Payments")).toBe(all.payments);
  expect(statValue("Amount")).toBe(all.amount);
  expect(statValue("Most accounts in a day")).toBe(all.accounts);
});

test("moving the slider asks the lab once, as of that day, after a pause and not on every step", async () => {
  const timeline = vi.spyOn(api, "timeline");
  mount();
  await ready();
  vi.useFakeTimers();

  move(10); await advance(100);
  move(20); await advance(100);
  move(30);
  expect(screen.getByRole("slider", { name: "As of" })).toHaveValue("30"); // the slider and its date follow at once
  expect(screen.getByText("Asking the lab…")).toBeInTheDocument();
  await advance(PAUSE_MS - 1);
  expect(timeline).toHaveBeenCalledTimes(1); // still only the first look at the whole range
  await advance(1);
  expect(timeline).toHaveBeenCalledTimes(2);
  expect(timeline).toHaveBeenLastCalledWith(RUN, dayAt(30)); // a bare date: the lab takes it as the end of that day
  await advance(PAUSE_MS * 3);
  expect(timeline).toHaveBeenCalledTimes(2); // and not again

  vi.useRealTimers();
  await screen.findByText("Showing the run's store up to the end of Oct 1, 2026.");
  expect(timeline).toHaveBeenCalledTimes(2);
});

test("letting go of the slider asks at once, and the pause that follows does not ask again", async () => {
  const timeline = vi.spyOn(api, "timeline");
  mount();
  await ready();
  vi.useFakeTimers();

  move(15);
  release();
  expect(timeline).toHaveBeenCalledTimes(2);
  expect(timeline).toHaveBeenLastCalledWith(RUN, dayAt(15));
  await advance(PAUSE_MS * 2);
  expect(timeline).toHaveBeenCalledTimes(2);

  release(); // letting go on the same day asks nothing
  expect(timeline).toHaveBeenCalledTimes(2);
});

test("the days after the as-of day are grey, the days up to it are not, and the line marks the end", async () => {
  mount();
  await ready();
  move(30); release();
  await screen.findByText("Showing the run's store up to the end of Oct 1, 2026.");
  const chart = screen.getByRole("img");
  const keys = (sel: string) => [...chart.querySelectorAll(sel)].map((b) => b.getAttribute("data-bar"));
  expect(keys("[data-bar][data-muted]")).toEqual(Array.from({ length: LAST - 30 }, (_, i) => dayAt(31 + i)));
  expect(keys("[data-bar]:not([data-muted])")).not.toContain(dayAt(31));
  expect(keys("[data-bar]:not([data-muted])")).toContain(dayAt(30));
  expect(chart.querySelector("[data-marker]")).toHaveAttribute("data-marker", dayAt(30));
});

test("the totals are the points up to the as-of day, wherever the slider is, including a day with no payments", async () => {
  mount();
  await ready();
  for (const i of [0, 3, 6, 7, 20, LAST]) {
    move(i); release();
    await waitFor(() => expect(statValue("Days")).toBe(String(i + 1)));
    const want = expected(dayAt(i));
    expect(statValue("Payments")).toBe(want.payments);
    expect(statValue("Amount")).toBe(want.amount);
    expect(statValue("Most accounts in a day")).toBe(want.accounts);
    expect(stat("Days")).toHaveTextContent(`${want.withPayments} with payments`);
  }
});

test("the days can be read as a table: the same points as the totals, up to the as-of day", async () => {
  mount();
  await ready();
  move(9); release();
  await screen.findByText("Showing the run's store up to the end of Sep 10, 2026.");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  const button = screen.getByRole("button", { name: "Show the days as a table" });
  expect(button).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  const rows = within(screen.getByRole("table", { name: "Days up to the as-of day" })).getAllByRole("row");
  expect(rows).toHaveLength(1 + expected(dayAt(9)).withPayments); // the header, then a row for each day with payments
  expect(rows[1]).toHaveTextContent("Sep 1, 2026");
  expect(rows[rows.length - 1]).toHaveTextContent("Sep 10, 2026");
});

test("the readout follows the pointer over a day, and goes back to the as-of day when it leaves", async () => {
  mount();
  await ready();
  move(10); release();
  await screen.findByText("Showing the run's store up to the end of Sep 11, 2026.");
  const readout = within(screen.getByRole("group", { name: "Selected day" }));
  expect(readout.getByText(/Sep 11, 2026/)).toBeInTheDocument();
  expect(readout.getByText(/the as-of day/)).toBeInTheDocument();

  const point = (day: string) => mockTimeline().points.find((p) => p.at.startsWith(day))!;
  const over = (label: string) => fireEvent.mouseEnter(screen.getByText(new RegExp(`${label}:`), { selector: "title" }).parentElement!);

  over("Sep 4, 2026"); // before the as-of day
  const early = point("2026-09-04");
  expect(readout.getByText(/Sep 4, 2026/)).toBeInTheDocument();
  expect(readout.getByText(`${INT(early.payments)} payments · ${MONEY(early.amount)} · ${INT(early.accounts)} accounts active`)).toBeInTheDocument();
  expect(readout.queryByText(/the as-of day/)).not.toBeInTheDocument();

  over("Sep 20, 2026"); // after it: the grey bars still say what they hold
  const late = point("2026-09-20");
  expect(readout.getByText(/after the as-of day/)).toBeInTheDocument();
  expect(readout.getByText(`${INT(late.payments)} payments · ${MONEY(late.amount)} · ${INT(late.accounts)} accounts active`)).toBeInTheDocument();

  over("Sep 7, 2026"); // a day with no payments says so
  expect(readout.getByText("No payments")).toBeInTheDocument();

  fireEvent.mouseLeave(screen.getByRole("img"));
  expect(readout.getByText(/Sep 11, 2026/)).toBeInTheDocument();
  expect(readout.getByText(/the as-of day/)).toBeInTheDocument();
});

test("Amount switches the bars to the amount of each day, on the same days", async () => {
  mount();
  await ready();
  const heights = () => [...screen.getByRole("img").querySelectorAll("[data-bar]")].map((b) => b.getAttribute("d"));
  const before = heights();
  fireEvent.click(screen.getByRole("tab", { name: "Amount" }));
  const chart = screen.getByRole("img", { name: "Amount per day, Sep 1, 2026 to Oct 15, 2026" });
  const after = [...chart.querySelectorAll("[data-bar]")].map((b) => b.getAttribute("d"));
  expect(after).toHaveLength(before.length);
  expect(after).not.toEqual(before);
  expect(screen.getByRole("tab", { name: "Amount" })).toHaveAttribute("aria-selected", "true");
});

// A lab that answers when the test says so: the whole range at once, each as-of question when its resolver is called.
function slowLab() {
  const answer: Record<string, () => void> = {};
  const spy = vi.spyOn(api, "timeline").mockImplementation((_id, asOf) =>
    asOf ? new Promise<TimelineData>((resolve) => { answer[asOf] = () => resolve(mockTimeline(asOf)); }) : Promise.resolve(mockTimeline()));
  return { answer, spy };
}

test("an answer that arrives after a newer question was asked does not overwrite the newer one", async () => {
  const { answer } = slowLab();
  mount();
  await ready();
  vi.useFakeTimers();
  move(10); await advance(PAUSE_MS); // asks for Sep 11 and gets no answer yet
  move(30); await advance(PAUSE_MS); // asks for Oct 1
  expect(Object.keys(answer)).toEqual([dayAt(10), dayAt(30)]);
  vi.useRealTimers();

  await act(async () => { answer[dayAt(30)](); }); // the newer answer arrives first
  await screen.findByText("Showing the run's store up to the end of Oct 1, 2026.");
  expect(statValue("Days")).toBe("31");
  expect(statValue("Payments")).toBe(expected(dayAt(30)).payments);

  await act(async () => { answer[dayAt(10)](); }); // then the old one, late
  expect(screen.getByText("Showing the run's store up to the end of Oct 1, 2026.")).toBeInTheDocument();
  expect(statValue("Days")).toBe("31");
  expect(statValue("Payments")).toBe(expected(dayAt(30)).payments);
  expect(screen.getByRole("img").querySelectorAll("[data-muted]")).toHaveLength(LAST - 30);
});

test("an answer to an older question is dropped even when it comes first, and the screen keeps waiting for the newer", async () => {
  const { answer } = slowLab();
  mount();
  await ready();
  vi.useFakeTimers();
  move(10); await advance(PAUSE_MS);
  move(30); await advance(PAUSE_MS);
  vi.useRealTimers();

  await act(async () => { answer[dayAt(10)](); });
  expect(screen.getByText("Asking the lab…")).toBeInTheDocument();
  expect(statValue("Days")).toBe(String(MOCK_TIMELINE_DAYS)); // still the first picture, not Sep 11's

  await act(async () => { answer[dayAt(30)](); });
  await screen.findByText("Showing the run's store up to the end of Oct 1, 2026.");
  expect(statValue("Days")).toBe("31");
});

test("a failed range load says why in a notice, and Try again asks again", async () => {
  const timeline = vi.spyOn(api, "timeline").mockRejectedValueOnce(new Error("run run-x has no store yet: its pull has not finished"));
  mount();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("The lab did not answer for this run");
  expect(alert).toHaveTextContent("its pull has not finished");
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await ready();
  expect(timeline).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a failed as-of question says so, keeps the picture it had, and can be asked again", async () => {
  mount();
  await ready();
  const timeline = vi.spyOn(api, "timeline").mockRejectedValueOnce(new Error("the store could not be read just now; try again in a moment"));
  move(12); release();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("The lab could not answer for Sep 13, 2026");
  expect(alert).toHaveTextContent("try again in a moment");
  expect(alert).toHaveTextContent("The screen still shows Oct 15, 2026.");
  expect(statValue("Days")).toBe(String(MOCK_TIMELINE_DAYS));
  expect(screen.queryByText("Asking the lab…")).not.toBeInTheDocument();

  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  await screen.findByText("Showing the run's store up to the end of Sep 13, 2026.");
  expect(timeline).toHaveBeenLastCalledWith(RUN, dayAt(12));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(statValue("Days")).toBe("13");
});

test("a store with no payments has no timeline, and says so", async () => {
  vi.spyOn(api, "timeline").mockResolvedValue({ start: "", end: "", as_of: null, points: [] });
  mount();
  expect(await screen.findByText("No payments in this run's store")).toBeInTheDocument();
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
});
