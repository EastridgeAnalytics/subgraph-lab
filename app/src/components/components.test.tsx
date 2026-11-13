import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { api, setMock } from "../api/client";
import { mockData } from "../api/mock";
import type { Recipe } from "../api/types";
import { defaultRecipe, RunProvider, STORAGE_KEY, stableJson } from "../state/run";
import { recipeToPython, recipeToYaml } from "./code";
import { formatInt, formatMB } from "./format";
import { ShowCode } from "./ShowCode";
import { Button, Card, Field, Notice, Page, Stat, Table, Tabs } from "./ui";

beforeEach(() => { setMock(true); window.localStorage.clear(); });
afterEach(() => { setMock(null); vi.restoreAllMocks(); });

// ---- the kit --------------------------------------------------------------------------------------------------
describe("the UI kit", () => {
  test("Page and Card give a screen a heading and labelled regions", () => {
    render(<Page title="Slice" purpose="Choose the part of the graph."><Card title="Estimate">inside</Card></Page>);
    expect(screen.getByRole("heading", { level: 1, name: "Slice" })).toBeInTheDocument();
    expect(screen.getByText("Choose the part of the graph.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Estimate" })).getByText("inside")).toBeInTheDocument();
  });

  test("Button is a button; busy and disabled stop clicks", () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button variant="primary" onClick={onClick}>Go</Button>);
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(<Button busy onClick={onClick}>Go</Button>);
    const busy = screen.getByRole("button", { name: "Go" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    fireEvent.click(busy);
    rerender(<Button disabled onClick={onClick}>Go</Button>);
    expect(screen.getByRole("button", { name: "Go" })).toBeDisabled();
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  test("Field labels an input, a select and a textarea, and ties the hint and the error to them", () => {
    render(<>
      <Field label="Name" hint="As on the card." error="Required" defaultValue="" />
      <Field as="select" label="Mode" defaultValue="b"><option value="a">A</option><option value="b">B</option></Field>
      <Field as="textarea" label="Query" defaultValue="MATCH (n) RETURN n" />
    </>);
    const name = screen.getByLabelText("Name");
    expect(name.tagName).toBe("INPUT");
    expect(name).toHaveAccessibleDescription("As on the card. Required");
    expect(name).toBeInvalid();
    expect(screen.getByLabelText("Mode").tagName).toBe("SELECT");
    expect(screen.getByLabelText("Mode")).toHaveValue("b");
    expect(screen.getByLabelText("Query")).toHaveValue("MATCH (n) RETURN n");
  });

  test("Stat shows a label and a value", () => {
    render(<Stat label="Nodes" value="412" />);
    expect(screen.getByText("Nodes")).toBeInTheDocument();
    expect(screen.getByText("412")).toBeInTheDocument();
  });

  test("Tabs switch on click and on the arrow keys", () => {
    function Demo() {
      const [v, setV] = useState("a");
      return <Tabs label="Demo" tabs={[{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }]} value={v} onChange={setV}>
        <p>panel {v}</p></Tabs>;
    }
    render(<Demo />);
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel a");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel b");
    fireEvent.click(screen.getByRole("tab", { name: "Alpha" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel a");
  });

  test("Notice announces errors and warnings at once, info and success politely", () => {
    render(<>
      <Notice kind="error" title="Broke">E</Notice><Notice kind="warning">W</Notice>
      <Notice kind="info">I</Notice><Notice kind="success">S</Notice>
    </>);
    expect(screen.getAllByRole("alert").map((n) => n.textContent)).toEqual(["BrokeE", "W"]);
    expect(screen.getAllByRole("status").map((n) => n.textContent)).toEqual(["I", "S"]);
  });

  test("Table shows rows, custom cells, and an empty state instead of a bare header", () => {
    const columns = [{ key: "key", header: "Key" }, { key: "n", header: "N", render: (r: { n: number }) => `#${r.n}` }];
    const { rerender } = render(<Table columns={columns} rows={[{ key: "A1", n: 1 }, { key: "A2", n: 2 }]} />);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("#2")).toBeInTheDocument();
    rerender(<Table columns={columns} rows={[]} empty="No hits." />);
    expect(screen.getByText("No hits.")).toBeInTheDocument();
  });
});

describe("formatters", () => {
  test("memory in MB and counts with separators", () => {
    expect(formatMB(1_332_000)).toBe("1.3 MB");
    expect(formatMB(250_000)).toBe("0.25 MB");
    expect(formatMB(1_513_000_000)).toBe("1,513.0 MB");
    expect(formatInt(1_900_000)).toBe("1,900,000");
  });
});

// ---- the code, in the browser ---------------------------------------------------------------------------------
describe("recipeToYaml and recipeToPython", () => {
  test("the default recipe, as the lab's dump_yaml writes it", () => {
    expect(recipeToYaml(defaultRecipe())).toBe([
      "name: default", "pull:", "  mode: schema", "  labels: []", "  types: []", "models:",
      "- node2vec", "- tgn", "- communities", "- velocity", "summaries: true", "",
    ].join("\n"));
  });

  test("a search recipe: nulls dropped, dates quoted, a Cypher query as a block", () => {
    const recipe: Recipe = {
      ...defaultRecipe(),
      pull: { mode: "cypher", query: "MATCH (a:Account)\nRETURN a", since: "2026-01-01" },
      search: { text: "ring: fast", account: null, k: 50, hops: 2 },
    };
    const yaml = recipeToYaml(recipe);
    expect(yaml).toContain('  since: "2026-01-01"\n');
    expect(yaml).toContain("  query: |-\n    MATCH (a:Account)\n    RETURN a\n");
    expect(yaml).toContain('search:\n  text: "ring: fast"\n  k: 50\n  hops: 2\n');
    expect(yaml).not.toContain("account");
    expect(yaml).not.toContain("null");
  });

  test("the Python builds the recipe and walks the run", () => {
    const withSearch = recipeToPython({ ...defaultRecipe(), search: { text: "ring", k: 50, hops: 2 } });
    expect(withSearch).toContain("recipe = Recipe.model_validate({");
    expect(withSearch).toContain('"models": ["node2vec", "tgn", "communities", "velocity"],');
    expect(withSearch).toContain('"summaries": True,');
    expect(withSearch).toContain("run = lab.Run.create(recipe)");
    for (const call of ["run.pull()", "run.first_pass()", "run.search()", "run.score()", "run.push()"]) {
      expect(withSearch).toContain(call);
    }
    expect(withSearch).not.toContain("None");
    const without = recipeToPython(defaultRecipe());
    expect(without).not.toContain("run.search()");
    expect(without).toContain("no search in this recipe");
  });
});

// ---- the Show code drawer -------------------------------------------------------------------------------------
describe("ShowCode", () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  });

  function open(recipe?: Recipe) {
    render(<RunProvider><ShowCode recipe={recipe} /></RunProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Show code" }));
    return screen.getByRole("dialog");
  }

  test("opens a side panel with the recipe as YAML and, on the other tab, the Python", () => {
    const recipe: Recipe = { ...defaultRecipe(), search: { text: "ring", k: 50, hops: 2 } };
    const dialog = open(recipe);
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("tab", { name: /yaml/i })).toHaveAttribute("aria-selected", "true");
    expect(dialog.querySelector("pre")!.textContent).toBe(recipeToYaml(recipe));
    fireEvent.click(within(dialog).getByRole("tab", { name: "Python" }));
    expect(dialog.querySelector("pre")!.textContent).toBe(recipeToPython(recipe));
    expect(dialog.querySelector("pre")!.textContent).toContain("lab.Run.create(recipe)");
    expect(within(dialog).getByText(/built in your browser/i)).toBeInTheDocument();
  });

  test("copy puts the open tab on the clipboard and says so", async () => {
    const recipe = defaultRecipe();
    const dialog = open(recipe);
    fireEvent.click(within(dialog).getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(recipeToYaml(recipe)));
    expect(await within(dialog).findByRole("button", { name: "Copied" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Python" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(recipeToPython(recipe)));
  });

  test("Escape and Close shut it and the focus goes back to the button", () => {
    open();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show code" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Show code" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("with a run id it shows the lab's code for that run", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe: defaultRecipe(), runId: "run-1", runRecipe: null }));
    const spy = vi.spyOn(api, "code");
    render(<RunProvider><ShowCode /></RunProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Show code" }));
    const dialog = screen.getByRole("dialog");
    await waitFor(() => expect(dialog.querySelector("pre")!.textContent).toBe(mockData.code.recipe_yaml));
    expect(spy).toHaveBeenCalledWith("run-1");
    expect(within(dialog).getByText("run-1")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Python" }));
    expect(dialog.querySelector("pre")!.textContent).toBe(mockData.code.python);
  });

  test("when the lab cannot give the run's code, it says so and shows the recipe's code instead", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe: defaultRecipe(), runId: "run-1", runRecipe: null }));
    vi.spyOn(api, "code").mockRejectedValue(new Error("no such run"));
    render(<RunProvider><ShowCode /></RunProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Show code" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("no such run");
    expect(dialog.querySelector("pre")!.textContent).toBe(recipeToYaml(defaultRecipe()));
  });

  test("a recipe edited after the run started shows the recipe's code, not the old run's", () => {
    const edited: Recipe = { ...defaultRecipe(), summaries: false };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      recipe: edited, runId: "run-1", runRecipe: stableJson(defaultRecipe()),
    }));
    const spy = vi.spyOn(api, "code");
    render(<RunProvider><ShowCode /></RunProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Show code" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelector("pre")!.textContent).toBe(recipeToYaml(edited));
    expect(within(dialog).getByText(/has changed since run run-1 started/i)).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });
});
