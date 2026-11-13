import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { api, setMock } from "../api/client";
import { mockModels } from "../api/mock";
import type { Recipe } from "../api/types";
import { STORAGE_KEY } from "../state/run";

beforeEach(() => { setMock(true); window.localStorage.clear(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); });

const mount = () => render(<MemoryRouter initialEntries={["/models"]}><App /></MemoryRouter>);
const saved = () => JSON.parse(window.localStorage.getItem(STORAGE_KEY)!) as { recipe: Recipe };
const card = (title: string) => screen.getByRole("heading", { level: 2, name: title }).closest("section") as HTMLElement;
const factorRow = (id: string) => within(card("Risk factors")).getByText(id, { selector: "code" }).closest("tr") as HTMLElement;
const loaded = () => screen.findByRole("heading", { level: 2, name: "Risk factors" });

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

test("a factor that a model writes says it counts 0 while that model is not ticked", async () => {
  mount();
  await loaded();
  expect(factorRow("tgn")).toHaveTextContent("Written by the tgn model.");
  fireEvent.click(screen.getByRole("checkbox", { name: "tgn" }));
  expect(factorRow("tgn")).toHaveTextContent("Counts 0 on the next run: the tgn model writes it, and it is not ticked.");
  expect(factorRow("shared_device_5plus")).not.toHaveTextContent("Counts 0");
});

test("a factor opens its code path and its factor file entry, and Escape closes it and gives focus back", async () => {
  mount();
  await loaded();
  const button = screen.getByRole("button", { name: "Show code for factor shared_device_5plus" });
  button.focus();
  fireEvent.click(button);
  const dialog = screen.getByRole("dialog", { name: "Risk factor: shared_device_5plus" });
  expect(dialog).toHaveTextContent("factors/shared_device.cypher");
  const yaml = dialog.querySelector("pre")!.textContent!;
  expect(yaml).toContain("- id: shared_device_5plus\n");
  expect(yaml).toContain("  kind: cypher\n  query: shared_device.cypher\n  threshold: 5\n  weight: 0.2\n");
  expect(yaml).not.toContain("edited");

  fireEvent.click(within(dialog).getByRole("tab", { name: "How the lab runs it" }));
  expect(dialog.querySelector("pre")!.textContent).toContain("min(1, value / threshold)");
  expect(within(dialog).getByText(/no route that returns a file's contents/)).toBeInTheDocument();

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(button).toHaveFocus();
});

test("a python factor and a model factor name their function and their example", async () => {
  mount();
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: "Show code for factor velocity_anomaly" }));
  let dialog = screen.getByRole("dialog", { name: "Risk factor: velocity_anomaly" });
  expect(dialog).toHaveTextContent("lab/scoring/velocity.py");
  expect(dialog.querySelector("pre")!.textContent).toContain("  function: lab.scoring.velocity:score\n");
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

  fireEvent.click(screen.getByRole("button", { name: "Show code for factor tgn" }));
  dialog = screen.getByRole("dialog", { name: "Risk factor: tgn" });
  expect(dialog).toHaveTextContent("examples/tgn");
  expect(dialog.querySelector("pre")!.textContent).toContain("  kind: model\n  source: examples/tgn\n  weight: 0.1\n");
  fireEvent.click(within(dialog).getByRole("tab", { name: "How the lab runs it" }));
  expect(dialog.querySelector("pre")!.textContent).toContain("artifacts/models/tgn.parquet");
});

test("a model opens its path and the recipe line that turns it on", async () => {
  mount();
  await loaded();
  fireEvent.click(within(card("Models")).getByRole("button", { name: "Show code for model tgn" }));
  const dialog = screen.getByRole("dialog", { name: "Model: tgn" });
  expect(dialog).toHaveTextContent("examples/tgn");
  expect(dialog.querySelector("pre")!.textContent).toContain("models:\n- tgn\n");
});

test("weights and thresholds can be edited here, show their effect, and go nowhere", async () => {
  const calls = [vi.spyOn(api, "startRun"), vi.spyOn(api, "sliceRun"), vi.spyOn(api, "disposition")];
  mount();
  await loaded();
  const before = window.localStorage.getItem(STORAGE_KEY);

  expect(card("Risk factors")).toHaveTextContent("Weights add up to 1.00.");
  expect(factorRow("shared_device_5plus")).toHaveTextContent("20%");
  fireEvent.change(screen.getByLabelText("Weight for shared_device_5plus"), { target: { value: "0.4" } });
  expect(factorRow("shared_device_5plus")).toHaveTextContent("edited here, not saved");
  expect(factorRow("shared_device_5plus")).toHaveTextContent("33%"); // 0.4 of 1.20
  expect(card("Risk factors")).toHaveTextContent("Weights add up to 1.20 with your edits.");
  fireEvent.change(screen.getByLabelText("Threshold for shared_device_5plus"), { target: { value: "4" } });

  fireEvent.click(screen.getByRole("button", { name: "Show code for factor shared_device_5plus" }));
  const yaml = screen.getByRole("dialog").querySelector("pre")!.textContent!;
  expect(yaml).toContain("  threshold: 4  # edited on this screen, not saved\n  weight: 0.4  # edited on this screen, not saved\n");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  expect(card("Risk factors")).toHaveTextContent(/is where they live, and the lab has no route that saves them/);
  expect(window.localStorage.getItem(STORAGE_KEY)).toBe(before); // nothing reached the recipe
  for (const call of calls) expect(call).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Reset edits" }));
  expect(screen.getByLabelText("Weight for shared_device_5plus")).toHaveValue(0.2);
  expect(screen.getByLabelText("Threshold for shared_device_5plus")).toHaveValue(5);
  expect(screen.queryByRole("button", { name: "Reset edits" })).not.toBeInTheDocument();
});

test("a weight or threshold that is not a number is marked and does not change the shares", async () => {
  mount();
  await loaded();
  const weight = screen.getByLabelText("Weight for shared_device_5plus");
  fireEvent.change(weight, { target: { value: "-1" } });
  expect(weight).toBeInvalid();
  expect(weight).toHaveAccessibleDescription("Use 0 or more.");
  expect(card("Risk factors")).toHaveTextContent("Weights add up to 1.00.");

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
  expect(rows).toEqual(["Member risk0.35", "Shared identifiers0.20", "Cycles0.15", "New accounts0.15", "Speed0.15"]);
  expect(within(policy).queryAllByRole("textbox")).toHaveLength(0);
  expect(within(policy).queryAllByRole("spinbutton")).toHaveLength(0);
  expect(within(policy).queryAllByRole("checkbox")).toHaveLength(0);
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
  expect(screen.getAllByRole("checkbox")).toHaveLength(4);
});

test("every control has a label", async () => {
  mount();
  await loaded();
  const main = screen.getByRole("main");
  for (const box of within(main).getAllByRole("checkbox")) expect(box).toHaveAccessibleName();
  for (const input of within(main).getAllByRole("spinbutton")) expect(input).toHaveAccessibleName();
  for (const button of within(main).getAllByRole("button")) expect(button).toHaveAccessibleName();
});
