import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { mockModels } from "../api/mock";
import type { Recipe } from "../api/types";
import { defaultRecipe, STORAGE_KEY } from "../state/run";

beforeEach(() => { setMock(true); window.localStorage.clear(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); });

const mount = () => render(<MemoryRouter initialEntries={["/models"]}><App /></MemoryRouter>);
const saved = () => JSON.parse(window.localStorage.getItem(STORAGE_KEY)!) as { recipe: Recipe };
const card = (title: string) => screen.getByRole("heading", { level: 2, name: title }).closest("section") as HTMLElement;
const factorRow = (id: string) => within(card("Risk factors")).getByText(id, { selector: "code" }).closest("tr") as HTMLElement;
const loaded = () => screen.findByRole("heading", { level: 2, name: "Risk factors" });
const FACTOR_IDS = mockModels.models.filter((m) => m.kind === "factor").map((m) => m.name);
const tick = (id: string) => screen.getByRole("checkbox", { name: `Use factor ${id}` });
// The same lab, with a policy that still takes a weighted mean.
const weightedMean = () => vi.spyOn(api, "models").mockResolvedValue({ ...mockModels, policy: { ...mockModels.policy, combine: "weighted_mean" } });
const openFactorCode = (id: string) => {
  fireEvent.click(screen.getByRole("button", { name: `Show code for factor ${id}` }));
  return screen.getByRole("dialog", { name: `Risk factor: ${id}` });
};
const shown = (dialog: HTMLElement) => dialog.querySelector("pre")!.textContent!;

test("it lists the four models, each ticked in the default recipe, with what it does and where its code is", async () => {
  mount();
  await loaded();
  const models = card("Models");
  for (const name of ["node2vec", "tgn", "communities", "velocity"]) {
    const box = within(models).getByRole("checkbox", { name });
    expect(box).toBeChecked();
    expect(box).toHaveAccessibleDescription(mockModels.models.find((m) => m.name === name)!.description);
  }
  expect(within(models).getByText("examples/tgn")).toBeInTheDocument();
  expect(within(models).getAllByRole("checkbox")).toHaveLength(4);
  expect(within(models).getByRole("status")).toHaveTextContent("The next run uses: node2vec, tgn, communities, velocity.");
});

test("it lists every factor from the factor file with its kind, weight and threshold, and what it is for", async () => {
  mount();
  await loaded();
  const factors = mockModels.models.filter((m) => m.kind === "factor");
  expect(factors).toHaveLength(7);
  expect(within(card("Risk factors")).getByRole("table", { name: "Risk factors" })).toBeInTheDocument();
  for (const info of factors) {
    const row = factorRow(info.name);
    expect(row).toHaveTextContent(info.factor!.name);
    expect(row).toHaveTextContent(info.description);
    expect(row).toHaveTextContent(info.factor!.kind);
    expect(within(row).getByLabelText(`Weight for ${info.name}`)).toHaveValue(info.factor!.weight);
    if (info.factor!.threshold == null) expect(within(row).queryByLabelText(`Threshold for ${info.name}`)).not.toBeInTheDocument();
    else expect(within(row).getByLabelText(`Threshold for ${info.name}`)).toHaveValue(info.factor!.threshold);
  }
  expect(within(factorRow("shared_device_5plus")).getByLabelText("Weight for shared_device_5plus")).toHaveValue(0.2);
  expect(within(factorRow("shared_device_5plus")).getByLabelText("Threshold for shared_device_5plus")).toHaveValue(5);
  expect(within(factorRow("tgn")).getByText("none")).toBeInTheDocument(); // a model factor has no threshold
  expect(card("Risk factors")).toHaveTextContent("version 9c1e4a7b02d3");
});

test("every factor has a tick box, all ticked while the recipe names no factors, and the status says every factor runs", async () => {
  mount();
  await loaded();
  for (const id of FACTOR_IDS) expect(tick(id)).toBeChecked();
  expect(within(card("Risk factors")).getAllByRole("checkbox")).toHaveLength(7);
  expect(saved().recipe.factors).toBeUndefined();
  expect(within(card("Risk factors")).getByText("The next run scores with every factor in the file.")).toBeInTheDocument();
});

test("ticking a model changes the recipe in the run context, in the lab's order, and Show code follows it", async () => {
  mount();
  await loaded();
  fireEvent.click(screen.getByRole("checkbox", { name: "tgn" }));
  expect(saved().recipe.models).toEqual(["node2vec", "communities", "velocity"]);
  expect(screen.getByRole("checkbox", { name: "tgn" })).not.toBeChecked();
  expect(within(card("Models")).getByRole("status")).toHaveTextContent("The next run uses: node2vec, communities, velocity.");

  fireEvent.click(screen.getByRole("checkbox", { name: "node2vec" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "tgn" }));
  expect(saved().recipe.models).toEqual(["tgn", "communities", "velocity"]);

  fireEvent.click(screen.getByRole("checkbox", { name: "node2vec" }));
  expect(saved().recipe.models).toEqual(["node2vec", "tgn", "communities", "velocity"]); // back in the lab's order

  fireEvent.click(screen.getByRole("button", { name: "Show code" }));
  expect(screen.getByRole("dialog").querySelector("pre")!.textContent).toContain("models:\n- node2vec\n- tgn\n- communities\n- velocity\n");
});

test("with no model ticked it says there will be no model output", async () => {
  mount();
  await loaded();
  for (const name of ["node2vec", "tgn", "communities", "velocity"]) fireEvent.click(screen.getByRole("checkbox", { name }));
  expect(saved().recipe.models).toEqual([]);
  expect(within(card("Models")).getByRole("status")).toHaveTextContent("No model is ticked");
});

test("a factor that a model writes says it changes nothing while that model is not ticked", async () => {
  mount();
  await loaded();
  expect(factorRow("tgn")).toHaveTextContent("Written by the tgn model.");
  fireEvent.click(screen.getByRole("checkbox", { name: "tgn" }));
  expect(factorRow("tgn")).toHaveTextContent("Changes nothing on the next run: the tgn model writes it, and it is not ticked.");
  expect(factorRow("tgn")).not.toHaveTextContent("Counts 0");
  expect(factorRow("shared_device_5plus")).not.toHaveTextContent("Changes nothing");
});

test("under a weighted mean policy the same factor says it counts 0, as that policy does", async () => {
  weightedMean();
  mount();
  await loaded();
  fireEvent.click(screen.getByRole("checkbox", { name: "tgn" }));
  expect(factorRow("tgn")).toHaveTextContent("Counts 0 on the next run: the tgn model writes it, and it is not ticked.");
});

// ---- choosing factors (decision 40) --------------------------------------------------------------------------------------
test("unticking a factor puts the others in recipe.factors, in the file's order, and the status and Show code follow", async () => {
  mount();
  await loaded();
  fireEvent.click(tick("tgn"));
  expect(saved().recipe.factors).toEqual(FACTOR_IDS.filter((id) => id !== "tgn"));
  expect(tick("tgn")).not.toBeChecked();
  expect(tick("shared_device_5plus")).toBeChecked();
  expect(within(card("Risk factors")).getByText(/The next run scores with:/)).toHaveTextContent(
    `The next run scores with: ${FACTOR_IDS.filter((id) => id !== "tgn").join(", ")}. The others are reported not chosen and do not run.`);
  expect(factorRow("tgn")).toHaveTextContent("Not chosen: it does not run on the next run and counts nothing.");
  expect(factorRow("tgn")).not.toHaveTextContent("Changes nothing");

  // another one off: still in the file's order, not the order they were clicked
  fireEvent.click(tick("shared_device_5plus"));
  expect(saved().recipe.factors).toEqual(FACTOR_IDS.filter((id) => id !== "tgn" && id !== "shared_device_5plus"));

  fireEvent.click(screen.getByRole("button", { name: "Show code" })); // the header's: the recipe on screen
  const yaml = screen.getByRole("dialog").querySelector("pre")!.textContent!;
  expect(yaml).toContain("models:\n- node2vec\n- tgn\n- communities\n- velocity\nfactors:\n- new_accounts_shared_identifier\n");
  expect(yaml).not.toContain("- shared_device_5plus");
});

test("ticking them all again clears recipe.factors, so the file's later factors are included", async () => {
  mount();
  await loaded();
  fireEvent.click(tick("tgn"));
  fireEvent.click(tick("velocity_anomaly"));
  expect(saved().recipe.factors).toHaveLength(5);
  fireEvent.click(tick("velocity_anomaly"));
  expect(saved().recipe.factors).toHaveLength(6);
  fireEvent.click(tick("tgn"));
  expect(saved().recipe).not.toHaveProperty("factors"); // unset: not an empty list and not the full list
  expect(within(card("Risk factors")).getByText("The next run scores with every factor in the file.")).toBeInTheDocument();
});

test("with no factor ticked the recipe lists none, and the screen says nothing scores", async () => {
  mount();
  await loaded();
  for (const id of FACTOR_IDS) fireEvent.click(tick(id));
  expect(saved().recipe.factors).toEqual([]);
  expect(within(card("Risk factors")).getByText("No factor is ticked, so no factor scores anything on the next run.")).toBeInTheDocument();
  fireEvent.click(tick("tgn"));
  expect(saved().recipe.factors).toEqual(["tgn"]);
});

test("a factor the recipe names and the file does not have is said, and the next tick drops it", async () => {
  const recipe: Recipe = { ...defaultRecipe(), factors: ["tgn", "retired_factor"] };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe, runId: null, runRecipe: null }));
  mount();
  await loaded();
  const warning = await screen.findByRole("alert");
  expect(warning).toHaveTextContent("The recipe names a factor the file does not have");
  expect(warning).toHaveTextContent("retired_factor");
  expect(tick("tgn")).toBeChecked();
  expect(tick("shared_device_5plus")).not.toBeChecked();
  fireEvent.click(tick("velocity_anomaly"));
  expect(saved().recipe.factors).toEqual(["velocity_anomaly", "tgn"]);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

// ---- the code panel (decision 43) ----------------------------------------------------------------------------------------
test("nothing is read from the lab until a code panel opens", async () => {
  const source = vi.spyOn(api, "source");
  mount();
  await loaded();
  expect(source).not.toHaveBeenCalled();
});

test("a factor opens the real file from the lab: its path as the tab, its language, its text, read only", async () => {
  const source = vi.spyOn(api, "source");
  mount();
  await loaded();
  const button = screen.getByRole("button", { name: "Show code for factor shared_device_5plus" });
  button.focus();
  const dialog = openFactorCode("shared_device_5plus");
  expect(dialog).toHaveTextContent("factors/shared_device.cypher");
  await waitFor(() => expect(shown(dialog)).toContain("MATCH (a:Account)-[:USES_DEVICE]->(d:Device)<-[:USES_DEVICE]-(other:Account)"));
  expect(source).toHaveBeenCalledWith("shared_device_5plus");
  expect(within(dialog).getByRole("tab", { name: "factors/shared_device.cypher" })).toHaveAttribute("aria-selected", "true");
  expect(within(dialog).getByText("Cypher")).toBeInTheDocument();
  expect(dialog.querySelector("pre code")).toHaveClass("language-cypher");
  expect(within(dialog).getByText(/read only/)).toBeInTheDocument();
  expect(dialog).not.toHaveTextContent(/no route that returns a file/);

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(button).toHaveFocus();
});

test("the text, language and path in the panel are the ones api.source answered", async () => {
  vi.spyOn(api, "source").mockResolvedValue({
    name: "velocity_anomaly", path: "lab/scoring/velocity.py", language: "python", text: "def score(run, factor, keys):\n    return 1\n",
  });
  mount();
  await loaded();
  const dialog = openFactorCode("velocity_anomaly");
  await waitFor(() => expect(shown(dialog)).toBe("def score(run, factor, keys):\n    return 1\n"));
  expect(within(dialog).getByRole("tab", { name: "lab/scoring/velocity.py" })).toBeInTheDocument();
  expect(within(dialog).getByText("Python")).toBeInTheDocument();
  expect(dialog.querySelector("pre code")).toHaveClass("language-python");
});

test("while the file loads the panel says so, and if it fails it says why and Try again asks again", async () => {
  const source = vi.spyOn(api, "source").mockRejectedValueOnce(new Error("'shared_device_5plus' has no source file the lab can show"));
  mount();
  await loaded();
  const dialog = openFactorCode("shared_device_5plus");
  expect(shown(dialog)).toBe("Loading the code…");
  const notice = await within(dialog).findByRole("alert");
  expect(notice).toHaveTextContent("The code did not load");
  expect(notice).toHaveTextContent("has no source file the lab can show");
  expect(shown(dialog)).toBe("The code did not load.");
  // the other tabs still work without the file
  fireEvent.click(within(dialog).getByRole("tab", { name: "Factor file entry" }));
  expect(shown(dialog)).toContain("- id: shared_device_5plus\n");

  fireEvent.click(within(notice).getByRole("button", { name: "Try again" }));
  fireEvent.click(within(dialog).getByRole("tab", { name: "factors/shared_device.cypher" }));
  await waitFor(() => expect(shown(dialog)).toContain("MATCH (a:Account)"));
  expect(source).toHaveBeenCalledTimes(2);
  expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
});

test("a panel asks again each time it opens", async () => {
  const source = vi.spyOn(api, "source");
  mount();
  await loaded();
  fireEvent.click(within(openFactorCode("shared_device_5plus")).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(source).toHaveBeenCalledTimes(1));
  openFactorCode("shared_device_5plus");
  await waitFor(() => expect(source).toHaveBeenCalledTimes(2));
});

test("a factor also has its factor file entry and how the lab runs it", async () => {
  mount();
  await loaded();
  const dialog = openFactorCode("shared_device_5plus");
  fireEvent.click(within(dialog).getByRole("tab", { name: "Factor file entry" }));
  const yaml = shown(dialog);
  expect(yaml).toContain("- id: shared_device_5plus\n");
  expect(yaml).toContain("  kind: cypher\n  query: shared_device.cypher\n  threshold: 5\n  weight: 0.2\n");
  expect(yaml).not.toContain("edited");

  fireEvent.click(within(dialog).getByRole("tab", { name: "How the lab runs it" }));
  expect(shown(dialog)).toContain("min(1, value / threshold)");
});

test("a python factor and a model factor open their real files and name their function and their example", async () => {
  mount();
  await loaded();
  let dialog = openFactorCode("velocity_anomaly");
  expect(dialog).toHaveTextContent("lab/scoring/velocity.py");
  await waitFor(() => expect(within(dialog).getByText("Python")).toBeInTheDocument());
  fireEvent.click(within(dialog).getByRole("tab", { name: "Factor file entry" }));
  expect(shown(dialog)).toContain("  function: lab.scoring.velocity:score\n");
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

  dialog = openFactorCode("tgn");
  expect(dialog).toHaveTextContent("examples/tgn");
  fireEvent.click(within(dialog).getByRole("tab", { name: "Factor file entry" }));
  expect(shown(dialog)).toContain("  kind: model\n  source: examples/tgn\n  weight: 0.05\n");
  fireEvent.click(within(dialog).getByRole("tab", { name: "How the lab runs it" }));
  expect(shown(dialog)).toContain("artifacts/models/tgn.parquet");
  expect(shown(dialog)).toContain("changes nothing in the score");
  expect(shown(dialog)).not.toMatch(/divisor|counts 0/i);
});

test("a model opens its example from the lab and the recipe line that turns it on", async () => {
  mount();
  await loaded();
  fireEvent.click(within(card("Models")).getByRole("button", { name: "Show code for model tgn" }));
  const dialog = screen.getByRole("dialog", { name: "Model: tgn" });
  await waitFor(() => expect(within(dialog).getByRole("tab", { name: "examples/tgn/example.py" })).toBeInTheDocument());
  expect(dialog).toHaveTextContent("examples/tgn/example.py");
  expect(shown(dialog)).toContain("def run_model");
  fireEvent.click(within(dialog).getByRole("tab", { name: "In the recipe" }));
  expect(shown(dialog)).toContain("models:\n- tgn\n");
});

// ---- weights, and what a weight means under noisy-or (decision 39) -------------------------------------------------------
test("the policy combines by noisy-or: a column says what each factor alone reaches, and no share or divisor is mentioned", async () => {
  mount();
  await loaded();
  const factors = card("Risk factors");
  const headers = within(within(factors).getByRole("table", { name: "Risk factors" })).getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers).toEqual(["Use", "Risk factor", "Kind", "Weight", "Alone it reaches", "Threshold", "Code"]);

  expect(factorRow("new_accounts_shared_identifier")).toHaveTextContent("0.65");
  expect(factorRow("new_accounts_shared_identifier")).toHaveTextContent("Alerts on its own");
  expect(factorRow("shared_device_5plus")).toHaveTextContent("0.20");
  expect(factorRow("shared_device_5plus")).toHaveTextContent("Cannot alert on its own");
  expect(factorRow("tgn")).toHaveTextContent("0.05");

  expect(factors).toHaveTextContent("The policy combines the factors by noisy-or.");
  expect(factors).toHaveTextContent("A factor that does not fire changes nothing, and adding a factor can only raise a score.");
  expect(factors).toHaveTextContent("An account alerts at 0.60 or above.");
  expect(factors).not.toHaveTextContent(/share of the score|weighted mean|divisor|Weights add up/i);
  expect(factors).not.toHaveTextContent(/%/);
  expect(within(card("Policy")).getByText("Account score combined by").closest("dl")).toHaveTextContent("noisy-or");
});

test("under a weighted mean policy the old wording is kept: a share of the score, and weights that add up", async () => {
  weightedMean();
  mount();
  await loaded();
  const factors = card("Risk factors");
  expect(within(factors).getByRole("columnheader", { name: "Share of the score" })).toBeInTheDocument();
  expect(within(factors).queryByRole("columnheader", { name: "Alone it reaches" })).not.toBeInTheDocument();
  const sum = mockModels.models.reduce((t, m) => t + (m.factor?.weight ?? 0), 0); // 2.45
  expect(factors).toHaveTextContent(`Weights add up to ${sum.toFixed(2)}.`);
  expect(factors).toHaveTextContent("The policy takes a weighted mean, so each factor's share is its weight over that total.");
  expect(factorRow("shared_device_5plus")).toHaveTextContent(`${Math.round((0.2 / sum) * 100)}%`);
  expect(factors).not.toHaveTextContent("noisy-or");
  // an unticked factor takes no share of the total
  fireEvent.click(tick("tgn"));
  expect(factorRow("tgn")).toHaveTextContent("not chosen");
  expect(factors).toHaveTextContent(`Weights add up to ${(sum - 0.05).toFixed(2)}.`);
});

test("weights and thresholds can be edited here, show their effect, and go nowhere", async () => {
  const calls = [vi.spyOn(api, "startRun"), vi.spyOn(api, "sliceRun"), vi.spyOn(api, "disposition")];
  mount();
  await loaded();
  const before = window.localStorage.getItem(STORAGE_KEY);

  expect(factorRow("shared_device_5plus")).toHaveTextContent("Cannot alert on its own");
  fireEvent.change(screen.getByLabelText("Weight for shared_device_5plus"), { target: { value: "0.7" } });
  expect(factorRow("shared_device_5plus")).toHaveTextContent("edited here, not saved");
  expect(factorRow("shared_device_5plus")).toHaveTextContent("0.70"); // alone it reaches
  expect(factorRow("shared_device_5plus")).toHaveTextContent("Alerts on its own"); // 0.70 is over the 0.60 line
  fireEvent.change(screen.getByLabelText("Threshold for shared_device_5plus"), { target: { value: "4" } });

  const dialog = openFactorCode("shared_device_5plus");
  fireEvent.click(within(dialog).getByRole("tab", { name: "Factor file entry" }));
  expect(shown(dialog)).toContain("  threshold: 4  # edited on this screen, not saved\n  weight: 0.7  # edited on this screen, not saved\n");
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

  expect(card("Risk factors")).toHaveTextContent(/is where they live, and the lab has no route that saves them/);
  expect(window.localStorage.getItem(STORAGE_KEY)).toBe(before); // nothing reached the recipe
  for (const call of calls) expect(call).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Reset edits" }));
  expect(screen.getByLabelText("Weight for shared_device_5plus")).toHaveValue(0.2);
  expect(screen.getByLabelText("Threshold for shared_device_5plus")).toHaveValue(5);
  expect(screen.queryByRole("button", { name: "Reset edits" })).not.toBeInTheDocument();
});

test("a weight outside 0 to 1 is marked with the range and changes nothing; 0 and 1 are fine", async () => {
  mount();
  await loaded();
  const weight = screen.getByLabelText("Weight for shared_device_5plus");
  fireEvent.change(weight, { target: { value: "1.5" } });
  expect(weight).toBeInvalid();
  expect(weight).toHaveAccessibleDescription("Use a number from 0 to 1.");
  expect(factorRow("shared_device_5plus")).not.toHaveTextContent("edited here, not saved");
  expect(factorRow("shared_device_5plus")).toHaveTextContent("0.20"); // still the file's weight

  fireEvent.change(weight, { target: { value: "-0.1" } });
  expect(weight).toHaveAccessibleDescription("Use a number from 0 to 1.");
  fireEvent.change(weight, { target: { value: "1" } });
  expect(weight).toBeValid();
  expect(factorRow("shared_device_5plus")).toHaveTextContent("1.00");
  fireEvent.change(weight, { target: { value: "0" } });
  expect(weight).toBeValid();
  fireEvent.change(weight, { target: { value: "" } });
  expect(weight).toHaveAccessibleDescription("Enter a number.");
});

test("under a weighted mean policy a weight only has to be 0 or more", async () => {
  weightedMean();
  mount();
  await loaded();
  const weight = screen.getByLabelText("Weight for shared_device_5plus");
  fireEvent.change(weight, { target: { value: "-1" } });
  expect(weight).toBeInvalid();
  expect(weight).toHaveAccessibleDescription("Use 0 or more.");
  fireEvent.change(weight, { target: { value: "1.5" } });
  expect(weight).toBeValid();
});

test("a threshold that is not a number is marked", async () => {
  mount();
  await loaded();
  const threshold = screen.getByLabelText("Threshold for shared_device_5plus");
  fireEvent.change(threshold, { target: { value: "0" } });
  expect(threshold).toBeInvalid();
  expect(threshold).toHaveAccessibleDescription("Use a number above 0.");
  fireEvent.change(threshold, { target: { value: "" } });
  expect(threshold).toHaveAccessibleDescription("Enter a number.");
});

test("the policy is shown and cannot be edited here", async () => {
  mount();
  await loaded();
  const policy = card("Policy");
  expect(within(policy).getByText("Alert threshold").closest("dl")).toHaveTextContent("0.60");
  expect(within(policy).getByText("Alert window").closest("dl")).toHaveTextContent("P30D");
  expect(within(policy).getByText("Smallest community scored").closest("dl")).toHaveTextContent("3");
  const weights = within(policy).getByRole("table", { name: "Community score weights" });
  const rows = within(weights).getAllByRole("row").slice(1).map((r) => r.textContent);
  expect(rows).toEqual(["Member risk0.65", "Shared identifiers0.20", "Cycles0.20", "New accounts0.20", "Speed0.20"]);
  expect(within(policy).queryAllByRole("textbox")).toHaveLength(0);
  expect(within(policy).queryAllByRole("spinbutton")).toHaveLength(0);
  expect(within(policy).queryAllByRole("checkbox")).toHaveLength(0);
});

test("the community weights card says how the parts combine from community.combine, not a fixed phrase", async () => {
  mount();
  await loaded();
  expect(card("Policy")).toHaveTextContent("A community's score combines these parts by noisy-or. Each weight is how much that part alone can say, from 0 to 1.");
  expect(card("Policy")).not.toHaveTextContent("weighted mean");
});

test("a community block that says weighted_mean, or says nothing, is a weighted mean", async () => {
  const policy = mockModels.policy as { community: Record<string, unknown> };
  const { combine: _gone, ...bare } = policy.community;
  const withCombine = (community: Record<string, unknown>) =>
    vi.spyOn(api, "models").mockResolvedValue({ ...mockModels, policy: { ...mockModels.policy, community } });

  withCombine({ ...bare, combine: "weighted_mean" });
  const first = mount();
  await loaded();
  expect(card("Policy")).toHaveTextContent("A community's score is the weighted mean of these parts.");
  first.unmount();

  vi.restoreAllMocks();
  withCombine(bare); // no combine at all
  mount();
  await loaded();
  expect(card("Policy")).toHaveTextContent("A community's score is the weighted mean of these parts.");
  expect(card("Policy")).not.toHaveTextContent("combines these parts by noisy-or");
});

test("when the lab cannot give the models it says why, and Try again asks again", async () => {
  const models = vi.spyOn(api, "models").mockRejectedValueOnce(new Error("the factor or policy file could not be read: bad YAML at line 4"));
  mount();
  const notice = await screen.findByRole("alert");
  expect(notice).toHaveTextContent("The models did not load");
  expect(notice).toHaveTextContent("bad YAML at line 4");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  fireEvent.click(within(notice).getByRole("button", { name: "Try again" }));
  await loaded();
  await waitFor(() => expect(models).toHaveBeenCalledTimes(2));
  expect(screen.getAllByRole("checkbox")).toHaveLength(11); // four models and seven factors
});

test("every control has a label", async () => {
  mount();
  await loaded();
  const main = screen.getByRole("main");
  for (const box of within(main).getAllByRole("checkbox")) expect(box).toHaveAccessibleName();
  for (const input of within(main).getAllByRole("spinbutton")) expect(input).toHaveAccessibleName();
  for (const button of within(main).getAllByRole("button")) expect(button).toHaveAccessibleName();
});
