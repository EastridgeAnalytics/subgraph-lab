import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { mockCommunityKeys, mockRunIds } from "../api/mock";
import { defaultRecipe, STORAGE_KEY } from "../state/run";

beforeEach(() => { setMock(true); window.localStorage.clear(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); });

const DONE = mockRunIds.done;
const KEYS = mockCommunityKeys;
const NAME_KEY = "subgraph-lab.reviewer";

const mount = (entry = `/results?run=${DONE}`) => render(<MemoryRouter initialEntries={[entry]}><App /></MemoryRouter>);
const accountsTable = () => screen.findByRole("table", { name: "Accounts and their risk scores" });
const communitiesTable = () => screen.findByRole("table", { name: "Communities and their risk scores" });
const dataRows = (table: HTMLElement) => within(table).getAllByRole("row").slice(1);
const firstKey = (table: HTMLElement) => within(dataRows(table)[0]).getAllByRole("cell")[0].textContent;
const rowOf = (table: HTMLElement, text: string) => within(table).getByText(text, { selector: "code" }).closest("tr") as HTMLElement;
const card = (title: string) => screen.getByRole("heading", { level: 2, name: title }).closest("section") as HTMLElement;
const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const nameYourself = (name = "Tim") => fireEvent.change(screen.getByLabelText("Your name"), { target: { value: name } });
const openCommunity = async (key: string) => {
  mount(`/results?run=${DONE}&community=${key}`);
  return screen.findByRole("heading", { level: 2, name: `Community ${key}` });
};

// ---- the run ---------------------------------------------------------------------------------------------------------------
test("with no run it says so and points to Slice, and asks for nothing", () => {
  const spies = [vi.spyOn(api, "accounts"), vi.spyOn(api, "communities"), vi.spyOn(api, "models")];
  mount("/results");
  expect(screen.getByRole("status")).toHaveTextContent("No run yet");
  expect(within(screen.getByRole("status")).getByRole("link", { name: "Slice" })).toHaveAttribute("href", "/slice");
  for (const s of spies) expect(s).not.toHaveBeenCalled();
});

test("a run id that is not a plain id is refused and nothing is asked", () => {
  const accounts = vi.spyOn(api, "accounts");
  mount("/results?run=../etc");
  expect(screen.getByRole("alert")).toHaveTextContent("That is not a run id");
  expect(accounts).not.toHaveBeenCalled();
});

test("it reads the run from the address", async () => {
  const accounts = vi.spyOn(api, "accounts");
  mount();
  await accountsTable();
  expect(accounts).toHaveBeenCalledWith(DONE, { limit: 100, offset: 0 });
  expect(card("Who is reviewing")).toBeInTheDocument();
  expect(within(card("Who is reviewing")).getByRole("link", { name: "Review and push" })).toHaveAttribute("href", `/review?run=${DONE}`);
});

test("it reads the run from the run context when the address has none", async () => {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe: defaultRecipe(), runId: DONE, runRecipe: null }));
  const accounts = vi.spyOn(api, "accounts");
  mount("/results");
  await accountsTable();
  expect(accounts).toHaveBeenCalledWith(DONE, { limit: 100, offset: 0 });
  expect(within(card("Who is reviewing")).getByRole("link", { name: "Review and push" })).toHaveAttribute("href", "/review");
});

// ---- accounts --------------------------------------------------------------------------------------------------------------
test("the accounts tab shows each account's score and its parts, the factors that fired, its community, alert and decision", async () => {
  mount();
  const table = await accountsTable();
  expect(screen.getByRole("tab", { name: "Accounts" })).toHaveAttribute("aria-selected", "true");
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent?.replace(/[▲▼↕]/g, ""))).toEqual(
    ["Account", "Risk score", "Factors that fired", "Community", "Alert", "Decision"]);

  const row = rowOf(table, "A1001");
  expect(row).toHaveTextContent("0.95");
  expect(row).toHaveTextContent("model 1.00, rules 0.91");
  expect(row).toHaveTextContent("shared_device_5plus score 1.00, weight 0.20");
  expect(row).toHaveTextContent("tgn score 0.80, weight 0.05");
  expect(within(row).getByRole("link", { name: `Open community ${KEYS.ring}` })).toBeInTheDocument();
  expect(row).toHaveTextContent("al-A1001");
  expect(within(row).getAllByRole("button")).toHaveLength(3);

  // the lowest score on the page has no alert
  fireEvent.click(within(table).getByRole("button", { name: "Risk score" })); // ascending
  expect(firstKey(table)).toBe("A1100");
  expect(dataRows(table)[0]).toHaveTextContent("no alert");
});

test("factor names from the models list are on the factor ids", async () => {
  mount();
  const table = await accountsTable();
  await waitFor(() => expect(within(rowOf(table, "A1001")).getByText("shared_device_5plus").closest("li")).toHaveAttribute("title", "Five or more accounts on one device"));
});

test("the accounts page through the server, 100 a page, and say which rows are shown", async () => {
  const accounts = vi.spyOn(api, "accounts");
  mount();
  const table = await accountsTable();
  expect(dataRows(table)).toHaveLength(100); // one server page, with no second pager inside it
  expect(screen.getByText("Showing 1 to 100 of 230 accounts")).toBeInTheDocument();
  expect(screen.getByText("230 accounts scored, highest risk score first. Click a column heading to sort the page you are on.")).toBeInTheDocument();
  const pager = screen.getByRole("navigation", { name: "Pages of Accounts and their risk scores" });
  expect(within(pager).getByText("Page 1 of 3")).toBeInTheDocument();
  expect(within(pager).getByRole("button", { name: "Previous" })).toBeDisabled();
  expect(screen.getAllByRole("navigation", { name: /^Pages of/ })).toHaveLength(1);

  fireEvent.click(within(pager).getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 101 to 200 of 230 accounts");
  expect(accounts).toHaveBeenLastCalledWith(DONE, { limit: 100, offset: 100 });
  expect(dataRows(table)).toHaveLength(100);
  expect(firstKey(table)).toBe("A1101");
  expect(within(pager).getByText("Page 2 of 3")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText(/^230 accounts scored/)).toHaveFocus()); // the pressed button is gone, so focus goes to the list

  fireEvent.click(within(pager).getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 201 to 230 of 230 accounts");
  expect(accounts).toHaveBeenLastCalledWith(DONE, { limit: 100, offset: 200 });
  expect(dataRows(table)).toHaveLength(30);
  expect(within(pager).getByRole("button", { name: "Next" })).toBeDisabled();

  fireEvent.click(within(pager).getByRole("button", { name: "Previous" }));
  await screen.findByText("Showing 101 to 200 of 230 accounts");
  expect(accounts).toHaveBeenLastCalledWith(DONE, { limit: 100, offset: 100 });
});

test("while the next page loads the page on screen stays, the buttons are off, and the range says it is loading", async () => {
  const real = api.accounts;
  let release!: () => void;
  vi.spyOn(api, "accounts").mockImplementation((id, page) => page?.offset === 100
    ? new Promise((res) => { release = () => res(real(id, page)); })
    : real(id, page));
  mount();
  const table = await accountsTable();
  const pager = screen.getByRole("navigation", { name: "Pages of Accounts and their risk scores" });
  fireEvent.click(within(pager).getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Loading page 2…")).toBeInTheDocument();
  expect(firstKey(table)).toBe("A1001"); // still the first page
  expect(within(pager).getByRole("button", { name: "Next" })).toBeDisabled();
  expect(within(pager).getByRole("button", { name: "Previous" })).toBeDisabled();
  release();
  await screen.findByText("Showing 101 to 200 of 230 accounts");
  expect(firstKey(table)).toBe("A1101");
});

test("a page that will not load says why, and Back to the first page gets out of it", async () => {
  const real = api.accounts;
  vi.spyOn(api, "accounts").mockImplementation((id, page) => page?.offset === 100
    ? Promise.reject(new Error("the staged scores could not be read just now"))
    : real(id, page));
  mount();
  await accountsTable();
  fireEvent.click(within(screen.getByRole("navigation", { name: "Pages of Accounts and their risk scores" })).getByRole("button", { name: "Next" }));
  const notice = await screen.findByRole("alert");
  expect(notice).toHaveTextContent("The accounts did not load");
  expect(notice).toHaveTextContent("could not be read just now");
  fireEvent.click(within(notice).getByRole("button", { name: "Back to the first page" }));
  expect(await screen.findByText("Showing 1 to 100 of 230 accounts")).toBeInTheDocument();
});

test("the offset is asked of the lab as a query: the client adds limit and offset to the path", async () => {
  const fetched: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string) => { fetched.push(path); return { ok: true, json: async () => ({ total: 0, rows: [] }) }; }));
  setMock(false); // the real client, with fetch stubbed
  try {
    await api.accounts(DONE, { limit: 100, offset: 100 });
    await api.communities(DONE, { limit: 50 });
    await api.accounts(DONE);
  } finally {
    vi.unstubAllGlobals();
  }
  expect(fetched).toEqual([
    `/api/runs/${DONE}/results/accounts?limit=100&offset=100`,
    `/api/runs/${DONE}/results/communities?limit=50`,
    `/api/runs/${DONE}/results/accounts`,
  ]);
});

test("a column sorts the page on screen on click, again to reverse, and marks the heading", async () => {
  mount();
  const table = await accountsTable();
  const heading = (name: string) => within(table).getByRole("columnheader", { name: new RegExp(`^${name}`) });
  expect(heading("Risk score")).toHaveAttribute("aria-sort", "descending"); // the lab's own order
  expect(firstKey(table)).toBe("A1001");

  fireEvent.click(within(table).getByRole("button", { name: "Risk score" }));
  expect(heading("Risk score")).toHaveAttribute("aria-sort", "ascending");
  expect(firstKey(table)).toBe("A1100"); // the lowest score on this page, not in the whole list
  fireEvent.click(within(table).getByRole("button", { name: "Risk score" }));
  expect(heading("Risk score")).toHaveAttribute("aria-sort", "descending");
  expect(firstKey(table)).toBe("A1001");

  fireEvent.click(within(table).getByRole("button", { name: "Account" }));
  expect(heading("Account")).toHaveAttribute("aria-sort", "ascending");
  expect(heading("Risk score")).toHaveAttribute("aria-sort", "none");
  expect(firstKey(table)).toBe("A1001");
  fireEvent.click(within(table).getByRole("button", { name: "Account" }));
  expect(firstKey(table)).toBe("A1100");

  fireEvent.click(within(table).getByRole("button", { name: "Factors that fired" })); // most factors first
  expect(within(dataRows(table)[0]).getAllByRole("listitem").length).toBe(4);
  expect(within(heading("Decision")).queryByRole("button")).not.toBeInTheDocument(); // not sortable
});

test("a sort stays when the next page comes", async () => {
  mount();
  const table = await accountsTable();
  fireEvent.click(within(table).getByRole("button", { name: "Account" })); // ascending by key
  fireEvent.click(within(screen.getByRole("navigation", { name: "Pages of Accounts and their risk scores" })).getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 101 to 200 of 230 accounts");
  expect(within(table).getByRole("columnheader", { name: /^Account/ })).toHaveAttribute("aria-sort", "ascending");
  expect(firstKey(table)).toBe("A1101");
});

test("a run that has not scored anything shows empty tables, not errors", async () => {
  mount(`/results?run=${mockRunIds.failed}`);
  const table = await accountsTable();
  expect(table).toHaveTextContent("No accounts were scored in this run yet.");
  tab("Communities");
  expect(await communitiesTable()).toHaveTextContent("No communities were found in this run yet.");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("accounts that will not load say why, and Try again asks again", async () => {
  const accounts = vi.spyOn(api, "accounts").mockRejectedValueOnce(new Error("the staged scores of run-x could not be read"));
  mount();
  const notice = await screen.findByRole("alert");
  expect(notice).toHaveTextContent("The accounts did not load");
  expect(notice).toHaveTextContent("could not be read");
  fireEvent.click(within(notice).getByRole("button", { name: "Try again" }));
  await accountsTable();
  expect(accounts).toHaveBeenCalledTimes(2);
});

test("factor names and weights that will not load are said, and the screen still works", async () => {
  vi.spyOn(api, "models").mockRejectedValue(new Error("the factor file could not be read"));
  mount();
  await accountsTable();
  expect(await screen.findByRole("alert")).toHaveTextContent("Factor names and policy weights did not load");
  tab("Communities");
  fireEvent.click(within(await communitiesTable()).getByRole("link", { name: `Open community ${KEYS.ring}` }));
  await screen.findByRole("heading", { level: 2, name: `Community ${KEYS.ring}` });
  expect(within(card("Score and its parts")).getAllByRole("meter")).toHaveLength(5);
  expect(card("Score and its parts")).not.toHaveTextContent("×");
});

// ---- communities -----------------------------------------------------------------------------------------------------------
test("the communities tab shows each community's size, score, the start of its summary with its source, and the decision", async () => {
  mount();
  await accountsTable();
  tab("Communities");
  const table = await communitiesTable();
  expect(screen.getByRole("tab", { name: "Communities" })).toHaveAttribute("aria-selected", "true");
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent?.replace(/[▲▼↕]/g, ""))).toEqual(
    ["Community", "Members", "Risk score", "Summary", "Decision"]);
  expect(dataRows(table)).toHaveLength(100);
  expect(screen.getByText("Showing 1 to 100 of 120 communities")).toBeInTheDocument();

  const ring = rowOf(table, KEYS.ring.slice(0, 12));
  expect(ring).toHaveTextContent("Source: api");
  expect(ring).toHaveTextContent("5 of the 7 accounts share device D-4400");
  expect(ring).toHaveTextContent("0.76");
  expect(rowOf(table, KEYS.agent)).toHaveTextContent("Source: agent");
  expect(rowOf(table, KEYS.template)).toHaveTextContent("Source: template");
  expect(rowOf(table, KEYS.noEvidence)).toHaveTextContent("No summary");

  // a decision the lab already holds shows on its row
  const decided = rowOf(table, KEYS.template);
  expect(within(decided).getByRole("button", { name: /as false positive$/ })).toHaveAttribute("aria-pressed", "true");
  expect(decided).toHaveTextContent("False positive.");
  expect(decided).toHaveTextContent("Staged: it will travel with the next push.");

  const pager = screen.getByRole("navigation", { name: "Pages of Communities and their risk scores" });
  expect(within(pager).getByText("Page 1 of 2")).toBeInTheDocument();
  expect(within(pager).getByRole("button", { name: "Previous" })).toBeDisabled();
  fireEvent.click(within(pager).getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 101 to 120 of 120 communities");
  expect(dataRows(table)).toHaveLength(20);
  expect(within(pager).getByRole("button", { name: "Next" })).toBeDisabled();
});

test("the communities page through the server too: Next asks for offset 100, and Previous comes back", async () => {
  const communities = vi.spyOn(api, "communities");
  mount();
  await accountsTable();
  tab("Communities");
  const table = await communitiesTable();
  expect(communities).toHaveBeenCalledWith(DONE, { limit: 100, offset: 0 });
  const pager = screen.getByRole("navigation", { name: "Pages of Communities and their risk scores" });
  fireEvent.click(within(pager).getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 101 to 120 of 120 communities");
  expect(communities).toHaveBeenLastCalledWith(DONE, { limit: 100, offset: 100 });
  // a community on the second page opens like any other
  fireEvent.click(within(dataRows(table)[0]).getByRole("link"));
  await screen.findByRole("heading", { level: 2, name: /^Community cf/ });
  fireEvent.click(screen.getByRole("button", { name: "All communities" }));
  await communitiesTable();
  fireEvent.click(within(screen.getByRole("navigation", { name: "Pages of Communities and their risk scores" })).getByRole("button", { name: "Previous" }));
  await screen.findByText("Showing 1 to 100 of 120 communities");
  expect(communities).toHaveBeenLastCalledWith(DONE, { limit: 100, offset: 0 });
});

test("another run starts on its first page", async () => {
  const accounts = vi.spyOn(api, "accounts");
  const first = mount();
  await accountsTable();
  fireEvent.click(within(screen.getByRole("navigation", { name: "Pages of Accounts and their risk scores" })).getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 101 to 200 of 230 accounts");
  first.unmount();
  mount(`/results?run=${mockRunIds.partialPush}`);
  await accountsTable();
  expect(accounts).toHaveBeenLastCalledWith(mockRunIds.partialPush, { limit: 100, offset: 0 });
  expect(screen.getByText("Showing 1 to 100 of 230 accounts")).toBeInTheDocument();
});

test("communities sort by members and by score", async () => {
  mount();
  await accountsTable();
  tab("Communities");
  const table = await communitiesTable();
  expect(within(table).getByRole("columnheader", { name: /^Risk score/ })).toHaveAttribute("aria-sort", "descending");
  fireEvent.click(within(table).getByRole("button", { name: "Members" }));
  expect(within(dataRows(table)[0]).getAllByRole("cell")[1]).toHaveTextContent("7"); // most members first
  fireEvent.click(within(table).getByRole("button", { name: "Members" }));
  expect(within(dataRows(table)[0]).getAllByRole("cell")[1]).toHaveTextContent("3");
});

// ---- one community ---------------------------------------------------------------------------------------------------------
test("a community opens to its decision, score, summary with its source, score parts, graph, evidence and similar communities", async () => {
  mount();
  await accountsTable();
  tab("Communities");
  fireEvent.click(within(await communitiesTable()).getByRole("link", { name: `Open community ${KEYS.ring}` }));
  const heading = await screen.findByRole("heading", { level: 2, name: `Community ${KEYS.ring}` });
  expect(heading).toHaveFocus();
  expect(screen.queryByRole("table", { name: "Communities and their risk scores" })).not.toBeInTheDocument(); // the list is put away

  expect(screen.getByRole("region", { name: "Your decision on this community" })).toBeInTheDocument();
  expect(screen.getByText("Risk score", { selector: "dt" }).closest("dl")).toHaveTextContent("0.76");
  expect(screen.getByText("Rank").closest("dl")).toHaveTextContent("1 of 120");

  const summary = card("Summary");
  expect(summary).toHaveTextContent("Source: api");
  expect(summary).toHaveTextContent("Written by the Anthropic API, model claude-sonnet-5-5.");
  expect(summary).toHaveTextContent("5 of the 7 accounts share device D-4400");
  expect(summary).toHaveTextContent("could indicate accounts operated together");
  expect(summary).toHaveTextContent("A family sharing one laptop, or a payroll account paying several people");
  expect(summary).toHaveTextContent("Every number is in the evidence: passed");
  expect(summary).toHaveTextContent("Evidence hash c3f9a1d2e4a1");

  const parts = card("Score and its parts");
  const meters = within(parts).getAllByRole("meter");
  expect(meters.map((m) => m.getAttribute("aria-label"))).toEqual(
    ["Member risk", "Shared identifiers", "Cycles", "New accounts", "Speed"]);
  expect(within(parts).getByRole("meter", { name: "Member risk" })).toHaveAttribute("aria-valuenow", "0.86");
  expect(parts).toHaveTextContent("Each part runs from 0 to 1. A part that is absent changes nothing; each part alone can raise the score up to its weight.");
  expect(parts).toHaveTextContent("0.86 × 0.65 = 0.56"); // a noisy-or: the weight as it is, not a share
  expect(parts).toHaveTextContent("The parts combine as 1 − Π(1 − w·v) = 0.76, the score the lab stored (0.76).");
  expect(parts).not.toHaveTextContent("add up to");
  expect(parts).not.toHaveTextContent("weighted mean");
  expect(parts).toHaveTextContent("Formed by a shared identifier"); // a tag, not a sixth part
  expect(within(parts).getAllByRole("meter")).toHaveLength(5);
  expect(within(parts).queryByRole("meter", { name: /formed/i })).not.toBeInTheDocument();

  const members = card("Members and links");
  const graph = within(members).getByRole("img", { name: /^Graph of community c3f9a1d2: 7 accounts, 2 shared identifiers and 7 payment links$/ });
  expect(graph.querySelectorAll("circle")).toHaveLength(7);
  expect(graph.querySelectorAll("rect")).toHaveLength(2);
  expect(graph.querySelectorAll("line")).toHaveLength(7 + 5 + 2);
  expect(members).toHaveTextContent("All 7 members, as keys");

  const evidence = card("Evidence");
  const shared = within(evidence).getByRole("table", { name: "Identifiers members share" });
  expect(shared).toHaveTextContent("D-4400");
  expect(rowOf(shared, "D-4400")).toHaveTextContent("5");
  expect(rowOf(shared, "e-093b2")).toHaveTextContent("2");
  expect(evidence.querySelector("dl")).toHaveTextContent("Cycles found3");
  expect(evidence.querySelector("dl")).toHaveTextContent("Time span48 hours");
  expect(evidence.querySelector("dl")).toHaveTextContent("First payment2026-10-03 14:02 UTC");
  expect(within(evidence).getByRole("table", { name: "Risk factors that fired in this community" })).toHaveTextContent("Five or more accounts on one device");
  expect(within(evidence).getByRole("table", { name: "Top members by risk score" })).toHaveTextContent("A1001");
  expect(evidence).toHaveTextContent("Ranks 1 of 120 communities by score. The median community has 3 members and a score of 0.41.");

  const like = card("Communities like this one");
  expect(within(like).getAllByRole("link").map((l) => l.textContent)).toEqual([KEYS.agent, KEYS.replaced, KEYS.template]);
  expect(like).toHaveTextContent("Source: agent");

  expect(document.body.textContent).not.toMatch(/fraud/i); // a risk score, never a verdict
});

test("a community formed by payments says so, and the parts and the foot are the same five", async () => {
  await openCommunity("cf017a3d1e");
  const parts = card("Score and its parts");
  expect(parts).toHaveTextContent("Formed by payments");
  expect(parts).not.toHaveTextContent("Formed by a shared identifier");
  expect(within(parts).getAllByRole("meter").map((m) => m.getAttribute("aria-label"))).toEqual(
    ["Member risk", "Shared identifiers", "Cycles", "New accounts", "Speed"]);
  expect(parts).toHaveTextContent(/The parts combine as 1 − Π\(1 − w·v\) = 0\.\d\d, the score the lab stored/);
  expect(parts.querySelector('[role="note"]')).toBeNull(); // the sum agrees with the stored score
});

test("the parts say what speed and cycles mean in the lab's words", async () => {
  await openCommunity(KEYS.ring);
  const parts = card("Score and its parts");
  expect(parts).toHaveTextContent("Money in and out of similar size within a day: the in_and_out_within_a_day factor's rows.");
  expect(parts).toHaveTextContent("Money that comes back to where it started, each payment after the one before it.");
});

test("under a weighted mean community policy the card keeps the old line and shares, and nothing is a noisy-or", async () => {
  const real = await api.models();
  const policy = real.policy as { community: Record<string, unknown> };
  vi.spyOn(api, "models").mockResolvedValue({ ...real, policy: { ...real.policy, community: { ...policy.community, combine: "weighted_mean" } } });
  await openCommunity(KEYS.ring);
  const parts = card("Score and its parts");
  await waitFor(() => expect(parts).toHaveTextContent("Each part runs from 0 to 1. The score is their weighted mean."));
  expect(parts).toHaveTextContent("× 0.45 = 0.39"); // 0.65 of a total weight of 1.45, times 0.86
  expect(parts).not.toHaveTextContent("1 − Π");
  expect(parts).toHaveTextContent("Formed by a shared identifier");
});

test("without the policy the card claims nothing about how the parts combine", async () => {
  vi.spyOn(api, "models").mockRejectedValue(new Error("the factor file could not be read"));
  await openCommunity(KEYS.ring);
  const parts = card("Score and its parts");
  expect(parts).toHaveTextContent("Each part runs from 0 to 1.");
  expect(parts).not.toHaveTextContent(/weighted mean|noisy/);
  expect(parts).toHaveTextContent("Formed by a shared identifier");
});

test("a link to a similar community opens it, and All communities goes back to the list", async () => {
  await openCommunity(KEYS.ring);
  fireEvent.click(within(card("Communities like this one")).getByRole("link", { name: KEYS.agent }));
  const heading = await screen.findByRole("heading", { level: 2, name: `Community ${KEYS.agent}` });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(card("Summary")).toHaveTextContent("Source: agent");
  expect(card("Summary")).toHaveTextContent("Written by a connected agent through describe_alert.");

  fireEvent.click(screen.getByRole("button", { name: "All communities" }));
  const table = await communitiesTable();
  expect(within(table).getByRole("link", { name: `Open community ${KEYS.ring}` })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { level: 2, name: /^Community / })).not.toBeInTheDocument();
});

test("opening a community from an account's row goes to the communities tab", async () => {
  mount();
  const table = await accountsTable();
  fireEvent.click(within(rowOf(table, "A1001")).getByRole("link", { name: `Open community ${KEYS.ring}` }));
  await screen.findByRole("heading", { level: 2, name: `Community ${KEYS.ring}` });
  expect(screen.getByRole("tab", { name: "Communities" })).toHaveAttribute("aria-selected", "true");
  tab("Accounts");
  expect(screen.queryByRole("heading", { level: 2, name: /^Community / })).not.toBeInTheDocument();
  expect((await accountsTable()).isConnected).toBe(true);
});

test("a summary from the template says so plainly, and does not claim a model wrote it", async () => {
  await openCommunity(KEYS.template);
  const summary = card("Summary");
  expect(summary).toHaveTextContent("Source: template");
  expect(summary).toHaveTextContent("Built from the evidence by a template. No model wrote this text.");
  expect(summary).toHaveTextContent("6 accounts form this community, with a risk score of 0.58.");
  expect(within(summary).queryByText(/replaced/i)).not.toBeInTheDocument();
  expect(summary).not.toHaveTextContent("Written by");
});

test("a summary whose checks failed shows that the template replaced it, why, and which check failed", async () => {
  await openCommunity(KEYS.replaced);
  const summary = card("Summary");
  expect(summary).toHaveTextContent("Source: template");
  const notice = within(summary).getByRole("alert");
  expect(notice).toHaveTextContent("A model's text was replaced by the template");
  expect(notice).toHaveTextContent("Every number is in the evidence");
  expect(notice).toHaveTextContent("A number in the text a model wrote (14) is not in the evidence.");
  expect(summary).toHaveTextContent("Every number is in the evidence: failed");
  expect(summary).toHaveTextContent("Every account named is a member: passed");
});

test("a community with no summary says why, and still shows the evidence", async () => {
  const real = await api.community(DONE, KEYS.ring);
  vi.spyOn(api, "community").mockResolvedValue({ ...real, summary: null });
  await openCommunity(KEYS.ring);
  expect(card("Summary")).toHaveTextContent("No summary for this community");
  expect(card("Evidence")).toHaveTextContent("D-4400");
});

test("a community the lab cannot open says why, and Try again asks again", async () => {
  const community = vi.spyOn(api, "community");
  mount(`/results?run=${DONE}&community=${KEYS.noEvidence}`);
  const notice = await screen.findByRole("alert");
  expect(notice).toHaveTextContent("This community did not load");
  expect(notice).toHaveTextContent("has no evidence object yet");
  fireEvent.click(within(notice).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(community).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole("alert")).toHaveTextContent("has no evidence object yet");
  fireEvent.click(screen.getByRole("button", { name: "All communities" }));
  await communitiesTable();
});

test("a community key that is not a plain key is refused and nothing is asked", async () => {
  const community = vi.spyOn(api, "community");
  mount(`/results?run=${DONE}&community=..%2Fetc`);
  expect(await screen.findByText("That is not a community key")).toBeInTheDocument();
  expect(community).not.toHaveBeenCalled();
});

test("a member picked from the list shows what links to it", async () => {
  await openCommunity(KEYS.ring);
  const members = card("Members and links");
  fireEvent.change(within(members).getByRole("combobox", { name: "Member to inspect" }), { target: { value: "A1001" } });
  expect(members).toHaveTextContent("A1001, risk score 0.95");
  expect(members).toHaveTextContent("2 payments to A1002, 1,200 in all.");
  expect(members).toHaveTextContent("2 payments from A1007, 3,600 in all.");
  expect(members).toHaveTextContent("Shares device D-4400 with 4 other accounts.");
  expect(members).toHaveTextContent("Shares email e-093b2 with 1 other account.");

  fireEvent.change(within(members).getByRole("combobox", { name: "Member to inspect" }), { target: { value: "Device:D-4400" } });
  expect(members).toHaveTextContent("Shared by 5 accounts: A1001, A1002, A1003, A1004, A1005.");
});

// ---- decisions -------------------------------------------------------------------------------------------------------------
test("decisions stay off until the reviewer has given a name, and say why", async () => {
  const decide = vi.spyOn(api, "disposition");
  mount();
  const table = await accountsTable();
  const confirm = within(rowOf(table, "A1001")).getByRole("button", { name: "Mark A1001 as confirmed" });
  expect(confirm).toBeDisabled();
  expect(confirm).toHaveAttribute("title", "Add your name above to record a decision.");
  fireEvent.click(confirm);
  expect(decide).not.toHaveBeenCalled();
  nameYourself("  ");
  expect(confirm).toBeDisabled();
  nameYourself("Tim Eastridge");
  expect(confirm).toBeEnabled();
});

test("a decision on an account goes to the lab with the reviewer's name, and the row shows it is staged for the next push", async () => {
  const decide = vi.spyOn(api, "disposition");
  mount();
  const table = await accountsTable();
  nameYourself("Tim Eastridge");
  expect(window.localStorage.getItem(NAME_KEY)).toBe("Tim Eastridge");

  fireEvent.click(within(rowOf(table, "A1001")).getByRole("button", { name: "Mark A1001 as confirmed" }));
  await waitFor(() => expect(rowOf(table, "A1001")).toHaveTextContent("Confirmed by Tim Eastridge."));
  expect(decide).toHaveBeenCalledTimes(1);
  expect(decide).toHaveBeenCalledWith(DONE, { kind: "account", key: "A1001", disposition: "confirmed", by: "Tim Eastridge" });
  const row = rowOf(table, "A1001");
  expect(row).toHaveTextContent("Staged: it will travel with the next push.");
  expect(within(row).getByRole("button", { name: "Mark A1001 as confirmed" })).toHaveAttribute("aria-pressed", "true");
  expect(within(row).getByRole("button", { name: "Mark A1001 as false positive" })).toHaveAttribute("aria-pressed", "false");

  // changing your mind replaces it
  fireEvent.click(within(row).getByRole("button", { name: "Mark A1001 as needs more" }));
  await waitFor(() => expect(rowOf(table, "A1001")).toHaveTextContent("Needs more by Tim Eastridge."));
  expect(decide).toHaveBeenLastCalledWith(DONE, { kind: "account", key: "A1001", disposition: "needs_more", by: "Tim Eastridge" });

  // a decision the lab already holds shows without a name, since it does not say who
  expect(rowOf(table, "A1003")).toHaveTextContent("Confirmed. Staged:");
});

test("the name is kept in this browser and is there the next time", async () => {
  const first = mount();
  await accountsTable();
  nameYourself("Joe Wanzala");
  first.unmount();
  mount();
  await accountsTable();
  expect(screen.getByLabelText("Your name")).toHaveValue("Joe Wanzala");
  expect(screen.getByRole("button", { name: "Mark A1001 as confirmed" })).toBeEnabled();
});

test("a second click while a decision is on its way sends nothing more", async () => {
  const decide = vi.spyOn(api, "disposition");
  mount();
  const table = await accountsTable();
  nameYourself();
  const button = within(rowOf(table, "A1002")).getByRole("button", { name: "Mark A1002 as false positive" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(rowOf(table, "A1002")).toHaveTextContent("False positive by Tim."));
  expect(decide).toHaveBeenCalledTimes(1);
});

test("a decision the lab refuses says why on its row, and the buttons come back", async () => {
  vi.spyOn(api, "disposition").mockRejectedValueOnce(new Error("the push store of run-x could not be read just now"));
  mount();
  const table = await accountsTable();
  nameYourself();
  fireEvent.click(within(rowOf(table, "A1001")).getByRole("button", { name: "Mark A1001 as confirmed" }));
  const row = rowOf(table, "A1001");
  await waitFor(() => expect(within(row).getByRole("alert")).toHaveTextContent("Not saved: the push store of run-x could not be read just now"));
  expect(within(row).getByRole("button", { name: "Mark A1001 as confirmed" })).toBeEnabled();
  expect(within(row).getByRole("button", { name: "Mark A1001 as confirmed" })).toHaveAttribute("aria-pressed", "false");

  fireEvent.click(within(row).getByRole("button", { name: "Mark A1001 as confirmed" }));
  await waitFor(() => expect(row).toHaveTextContent("Confirmed by Tim."));
  expect(within(row).queryByRole("alert")).not.toBeInTheDocument();
});

test("a decision the lab took but did not stage says it will not travel", async () => {
  vi.spyOn(api, "disposition").mockResolvedValueOnce({ ok: true, staged: false });
  mount();
  const table = await accountsTable();
  nameYourself();
  fireEvent.click(within(rowOf(table, "A1001")).getByRole("button", { name: "Mark A1001 as confirmed" }));
  await waitFor(() => expect(rowOf(table, "A1001")).toHaveTextContent("Recorded, but not staged: it will not travel with the next push."));
});

test("a community takes a decision from its row and from its own page, and both show the same one", async () => {
  const decide = vi.spyOn(api, "disposition");
  mount();
  await accountsTable();
  nameYourself();
  tab("Communities");
  const table = await communitiesTable();
  const label = `community ${KEYS.ring.slice(0, 8)}`;
  fireEvent.click(within(rowOf(table, KEYS.ring.slice(0, 12))).getByRole("button", { name: `Mark ${label} as needs more` }));
  await waitFor(() => expect(rowOf(table, KEYS.ring.slice(0, 12))).toHaveTextContent("Needs more by Tim."));
  expect(decide).toHaveBeenCalledWith(DONE, { kind: "community", key: KEYS.ring, disposition: "needs_more", by: "Tim" });

  fireEvent.click(within(table).getByRole("link", { name: `Open community ${KEYS.ring}` }));
  await screen.findByRole("heading", { level: 2, name: `Community ${KEYS.ring}` });
  const mine = screen.getByRole("region", { name: "Your decision on this community" });
  expect(within(mine).getByRole("button", { name: `Mark ${label} as needs more` })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(mine).getByRole("button", { name: `Mark ${label} as false positive` }));
  await waitFor(() => expect(mine).toHaveTextContent("False positive by Tim."));
  expect(decide).toHaveBeenLastCalledWith(DONE, { kind: "community", key: KEYS.ring, disposition: "false_positive", by: "Tim" });

  fireEvent.click(screen.getByRole("button", { name: "All communities" }));
  const back = await communitiesTable();
  expect(within(rowOf(back, KEYS.ring.slice(0, 12))).getByRole("button", { name: `Mark ${label} as false positive` })).toHaveAttribute("aria-pressed", "true");
});

test("a row's three decisions are one group with a name, and every control on the screen has a label", async () => {
  mount();
  const table = await accountsTable();
  expect(within(rowOf(table, "A1001")).getByRole("group", { name: "Decision for A1001" })).toBeInTheDocument();
  const main = screen.getByRole("main");
  for (const control of [...within(main).getAllByRole("button"), ...within(main).getAllByRole("textbox"), ...within(main).getAllByRole("link")]) {
    expect(control).toHaveAccessibleName();
  }
});
