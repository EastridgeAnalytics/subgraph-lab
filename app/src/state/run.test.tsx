import { fireEvent, render, screen } from "@testing-library/react";
import { defaultRecipe, RunProvider, STORAGE_KEY, useRecipe, useRunContext, useRunId } from "./run";

beforeEach(() => window.localStorage.clear());

function Probe() {
  const [recipe, setRecipe] = useRecipe();
  const [runId, setRunId] = useRunId();
  const { recipeChangedSinceRun, reset } = useRunContext();
  return (
    <div>
      <p data-testid="name">{recipe.name}</p>
      <p data-testid="models">{recipe.models.join(",")}</p>
      <p data-testid="run">{runId ?? "none"}</p>
      <p data-testid="changed">{String(recipeChangedSinceRun)}</p>
      <button onClick={() => setRecipe((r) => ({ ...r, name: "mine", models: ["tgn"] }))}>edit</button>
      <button onClick={() => setRunId("run-7")}>store run</button>
      <button onClick={reset}>reset</button>
    </div>
  );
}

const mount = () => render(<RunProvider><Probe /></RunProvider>);

test("starts from the default recipe and no run", () => {
  mount();
  expect(screen.getByTestId("name")).toHaveTextContent(defaultRecipe().name);
  expect(screen.getByTestId("models")).toHaveTextContent("node2vec,tgn,communities,velocity");
  expect(screen.getByTestId("run")).toHaveTextContent("none");
});

test("the recipe and the run id survive a reload from localStorage", () => {
  const first = mount();
  fireEvent.click(screen.getByText("edit"));
  fireEvent.click(screen.getByText("store run"));
  first.unmount();

  mount(); // a fresh provider reads what the first one saved
  expect(screen.getByTestId("name")).toHaveTextContent("mine");
  expect(screen.getByTestId("models")).toHaveTextContent("tgn");
  expect(screen.getByTestId("run")).toHaveTextContent("run-7");
  expect(screen.getByTestId("changed")).toHaveTextContent("false");
});

test("editing the recipe after a run started says the recipe has changed since", () => {
  mount();
  fireEvent.click(screen.getByText("store run"));
  expect(screen.getByTestId("changed")).toHaveTextContent("false");
  fireEvent.click(screen.getByText("edit"));
  expect(screen.getByTestId("changed")).toHaveTextContent("true");
});

test("reset goes back to the default recipe and no run", () => {
  mount();
  fireEvent.click(screen.getByText("edit"));
  fireEvent.click(screen.getByText("store run"));
  fireEvent.click(screen.getByText("reset"));
  expect(screen.getByTestId("name")).toHaveTextContent("default");
  expect(screen.getByTestId("run")).toHaveTextContent("none");
});

test("text in storage that is not a recipe falls back to the default", () => {
  window.localStorage.setItem(STORAGE_KEY, "{not json");
  const first = mount();
  expect(screen.getByTestId("name")).toHaveTextContent("default");
  first.unmount();
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ recipe: { name: "x" }, runId: 5 }));
  mount();
  expect(screen.getByTestId("name")).toHaveTextContent("default");
  expect(screen.getByTestId("run")).toHaveTextContent("none");
});

test("a hook outside the provider says what is missing", () => {
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});
  expect(() => render(<Probe />)).toThrow(/RunProvider/);
  spy.mockRestore();
});
