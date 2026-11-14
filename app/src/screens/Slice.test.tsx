import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { mockData } from "../api/mock";
import type { Recipe } from "../api/types";
import { defaultRecipe, STORAGE_KEY } from "../state/run";

beforeEach(() => { setMock(true); window.localStorage.clear(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); });

const mount = () => render(<MemoryRouter initialEntries={["/slice"]}><App /></MemoryRouter>);
const estimateButton = () => screen.getByRole("button", { name: "Estimate" });
const runButton = () => screen.getByRole("button", { name: "Run this slice" });
const tab = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const saved = () => JSON.parse(window.localStorage.getItem(STORAGE_KEY)!) as { recipe: Recipe; runId: string | null };

function searchFor(text: string) {
  tab("By search");
  fireEvent.change(screen.getByLabelText("Search text"), { target: { value: text } });
}

test("it offers three ways to slice, each with its own fields and a default in every one", () => {
  mount();
  expect(screen.getByRole("tablist", { name: "Slice mode" })).toBeInTheDocument();
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["By schema", "By Cypher", "By search"]);

  // by schema is first; empty labels, types and window mean everything in the mapping
  expect(screen.getByRole("tab", { name: "By schema" })).toHaveAttribute("aria-selected", "true");
  for (const label of ["Labels", "Relationship types", "Since", "Until"]) expect(screen.getByLabelText(label)).toHaveValue("");

  tab("By Cypher");
  expect((screen.getByLabelText("Cypher query") as HTMLTextAreaElement).value).toMatch(/^MATCH .*\nRETURN a, t, b$/s);

  tab("By search");
  expect(screen.getByLabelText("Search text")).toHaveValue("");
  expect(screen.getByLabelText("Account key")).toHaveValue("");
  expect(screen.getByLabelText(/hits to keep/i)).toHaveValue(50);
  expect(screen.getByLabelText(/account steps out from each hit/i)).toHaveValue(1); // a hop is one account-to-account step (decision 41)
  expect(screen.getByText("Default 1. A step is a payment or a shared identifier.")).toBeInTheDocument();
  expect(screen.getByText("Search needs the first pass")).toBeInTheDocument();
});

test("Show code is on the screen and shows the recipe being edited", () => {
  mount();
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "Account, Device" } });
  fireEvent.click(screen.getByRole("button", { name: "Show code" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.querySelector("pre")!.textContent).toContain("  labels:\n  - Account\n  - Device\n");
});

test("Run stays off until the slice is estimated, then Estimate shows the size before anything runs", async () => {
  const start = vi.spyOn(api, "startRun");
  mount();
  expect(runButton()).toBeDisabled();
  expect(screen.getByText(/estimate first/i)).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Estimate" })).not.toBeInTheDocument();

  fireEvent.click(estimateButton());
  const panel = await screen.findByRole("region", { name: "Estimate" });
  expect(within(panel).getByText("Nodes")).toBeInTheDocument();
  expect(within(panel).getByText("9,800")).toBeInTheDocument();
  expect(within(panel).getByText("41,000")).toBeInTheDocument();
  expect(within(panel).getByText("32.2 MB")).toBeInTheDocument();
  expect(within(panel).queryByText("Hits")).not.toBeInTheDocument(); // a schema slice has no hits
  expect(runButton()).toBeEnabled();
  expect(start).not.toHaveBeenCalled();
});

test("Estimate in search mode shows the hits, which list found them, the hops, the size and the memory", async () => {
  const estimate = vi.spyOn(api, "sliceEstimate");
  mount();
  searchFor("accounts moving money in a ring");
  fireEvent.click(estimateButton());

  const panel = await screen.findByRole("region", { name: "Estimate" });
  const hits = within(panel).getByRole("table", { name: "Search hits" });
  const row = within(hits).getByText("A0011").closest("tr")!;
  expect(within(row).getByText("0.0328")).toBeInTheDocument();
  expect(within(row).getByText("Vector, Full text")).toBeInTheDocument();
  expect(within(panel).getByText("Hops")).toBeInTheDocument();
  expect(within(panel).getByText("1", { selector: "dd" })).toBeInTheDocument();
  expect(within(panel).getByText("412")).toBeInTheDocument();
  expect(within(panel).getByText("1,630")).toBeInTheDocument();
  expect(within(panel).getByText("1.3 MB")).toBeInTheDocument();
  expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();

  const sent = estimate.mock.calls[0][0].recipe!;
  expect(sent.search).toEqual({ text: "accounts moving money in a ring", account: null, k: 50, hops: 1 });
  expect(sent.pull.mode).toBe("schema");
});

test("over the cap: a warning says so and Run is disabled", async () => {
  mount();
  searchFor("ring");
  fireEvent.change(screen.getByLabelText(/account steps out from each hit/i), { target: { value: "5" } });
  fireEvent.click(estimateButton());

  const panel = await screen.findByRole("region", { name: "Estimate" });
  const warning = within(panel).getByRole("alert");
  expect(warning).toHaveTextContent("Over the cap");
  expect(warning).toHaveTextContent("480,000");
  expect(warning).toHaveTextContent("200,000");
  expect(warning).toHaveTextContent(/lower k or the hops/i);
  expect(within(panel).getByText(mockData.estimateOverCap.note!)).toBeInTheDocument();
  expect(runButton()).toBeDisabled();
});

test("editing a field drops the estimate, so Run never runs something that was not estimated", async () => {
  mount();
  fireEvent.click(estimateButton());
  await screen.findByRole("region", { name: "Estimate" });
  expect(runButton()).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "Account" } });
  expect(screen.queryByRole("region", { name: "Estimate" })).not.toBeInTheDocument();
  expect(runButton()).toBeDisabled();
});

test("Run this slice starts the run with the recipe on screen, stores the run id and goes to /run", async () => {
  const start = vi.spyOn(api, "startRun");
  mount();
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "Account, Device" } });
  fireEvent.change(screen.getByLabelText("Relationship types"), { target: { value: "TX" } });
  fireEvent.change(screen.getByLabelText("Since"), { target: { value: "2026-05-01" } });
  fireEvent.click(estimateButton());
  await screen.findByRole("region", { name: "Estimate" });
  fireEvent.click(runButton());

  expect(await screen.findByRole("list", { name: "Steps" })).toBeInTheDocument(); // navigated to /run, which is built now (2e)
  expect(screen.getByRole("link", { name: "Run" })).toHaveAttribute("aria-current", "page");
  expect(start).toHaveBeenCalledTimes(1);
  const recipe = start.mock.calls[0][0];
  expect(recipe.pull).toEqual({ mode: "schema", labels: ["Account", "Device"], types: ["TX"], since: "2026-05-01", until: null });
  expect(recipe.search).toBeNull();
  expect(recipe.models).toEqual(defaultRecipe().models);

  const stored = saved();
  expect(stored.runId).toBe("run-20261112T090000Z-mock01");
  expect(stored.recipe.pull.labels).toEqual(["Account", "Device"]);
});

test("a Cypher slice sends the query; an empty query cannot be estimated", async () => {
  const estimate = vi.spyOn(api, "sliceEstimate");
  mount();
  tab("By Cypher");
  fireEvent.change(screen.getByLabelText("Cypher query"), { target: { value: "MATCH p=()-[:TX]->() RETURN p" } });
  fireEvent.click(estimateButton());
  await screen.findByRole("region", { name: "Estimate" });
  expect(estimate.mock.calls[0][0].recipe!.pull).toEqual({ mode: "cypher", query: "MATCH p=()-[:TX]->() RETURN p" });
  expect(estimate.mock.calls[0][0].recipe!.search).toBeNull();

  fireEvent.change(screen.getByLabelText("Cypher query"), { target: { value: "  " } });
  expect(estimateButton()).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Use the example" }));
  expect(estimateButton()).toBeEnabled();
});

test("search needs a sentence or an account key before it can be estimated", () => {
  mount();
  tab("By search");
  expect(estimateButton()).toBeDisabled();
  expect(screen.getByText("Give a sentence, an account key, or both.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Account key"), { target: { value: "A0011" } });
  expect(estimateButton()).toBeEnabled();
});

test("a bad k is flagged on the field", () => {
  mount();
  searchFor("ring");
  fireEvent.change(screen.getByLabelText(/hits to keep/i), { target: { value: "0" } });
  expect(screen.getByLabelText(/hits to keep/i)).toBeInvalid();
  expect(estimateButton()).toBeDisabled();
});

test("API errors show in a notice and the screen stays usable", async () => {
  vi.spyOn(api, "sliceEstimate").mockRejectedValueOnce(new Error("run store is empty"));
  const start = vi.spyOn(api, "startRun").mockRejectedValueOnce(new Error("lab is busy"));
  mount();
  fireEvent.click(estimateButton());
  expect(await screen.findByText("run store is empty")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("The estimate did not come back");
  expect(runButton()).toBeDisabled();

  fireEvent.click(estimateButton());
  await screen.findByRole("region", { name: "Estimate" });
  fireEvent.click(runButton());
  expect(await screen.findByText("lab is busy")).toBeInTheDocument();
  expect(start).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("heading", { level: 1, name: "Slice" })).toBeInTheDocument();
  expect(saved().runId).toBeNull();
});

test("the slice survives a reload: the fields come back from the stored recipe", () => {
  const recipe: Recipe = { ...defaultRecipe(), search: { text: "ring", account: null, k: 20, hops: 3 } };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe, runId: null, runRecipe: null }));
  mount();
  expect(screen.getByRole("tab", { name: "By search" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("Search text")).toHaveValue("ring");
  expect(screen.getByLabelText(/hits to keep/i)).toHaveValue(20);
  expect(screen.getByLabelText(/account steps out from each hit/i)).toHaveValue(3); // not the default, so it came from the stored recipe
});

test("busy state while the estimate is on its way", async () => {
  let release!: () => void;
  vi.spyOn(api, "sliceEstimate").mockImplementation(() => new Promise((res) => { release = () => res(mockData.estimateSchema); }));
  mount();
  fireEvent.click(estimateButton());
  expect(estimateButton()).toBeDisabled();
  expect(estimateButton()).toHaveAttribute("aria-busy", "true");
  release();
  await waitFor(() => expect(estimateButton()).toBeEnabled());
});
