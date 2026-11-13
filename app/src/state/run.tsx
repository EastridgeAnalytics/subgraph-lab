// The run context: the current recipe and the current run id, shared by every screen and kept in localStorage so a
// reload keeps them. Nothing secret goes in here: the Connect screen's password lives in its own component state.
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type Dispatch, type ReactNode, type SetStateAction,
} from "react";
import type { Recipe } from "../api/types";

export const STORAGE_KEY = "subgraph-lab.run";

// The default recipe (lab/contracts/recipe.py): no search, every model, summaries on, write-back left to the lab.
export function defaultRecipe(): Recipe {
  return {
    name: "default",
    pull: { mode: "schema", labels: [], types: [] },
    search: null,
    models: ["node2vec", "tgn", "communities", "velocity"],
    summaries: true,
  };
}

// JSON with sorted keys, so two recipes with the same content compare equal whatever their key order.
export function stableJson(value: unknown): string {
  return JSON.stringify(value, (_k, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v);
}

interface State {
  recipe: Recipe;
  runId: string | null;
  runRecipe: string | null; // stableJson of the recipe when the run id was stored; says whether it was edited since
}

function fresh(): State {
  return { recipe: defaultRecipe(), runId: null, runRecipe: null };
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function load(): State {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return fresh();
    const saved: unknown = JSON.parse(raw);
    if (!isObject(saved) || !isObject(saved.recipe) || !isObject(saved.recipe.pull) || !Array.isArray(saved.recipe.models)) {
      return fresh();
    }
    const base = defaultRecipe();
    const recipe = { ...base, ...(saved.recipe as object), pull: { ...base.pull, ...(saved.recipe.pull as object) } } as Recipe;
    return {
      recipe,
      runId: typeof saved.runId === "string" && saved.runId ? saved.runId : null,
      runRecipe: typeof saved.runRecipe === "string" ? saved.runRecipe : null,
    };
  } catch {
    return fresh(); // storage blocked or the text is not JSON
  }
}

export interface RunContextValue {
  recipe: Recipe;
  setRecipe: Dispatch<SetStateAction<Recipe>>;
  runId: string | null;
  /** Stores the run id with a snapshot of the recipe it was started from. `null` forgets the run. */
  setRunId: (id: string | null) => void;
  /** True when a run is stored and the recipe has been edited since that run started. */
  recipeChangedSinceRun: boolean;
  /** Back to the default recipe and no run. */
  reset: () => void;
}

const RunContext = createContext<RunContextValue | null>(null);

export function RunProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>(load);

  useEffect(() => {
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* storage blocked: the context still works */ }
  }, [state]);

  const setRecipe = useCallback<Dispatch<SetStateAction<Recipe>>>((next) => {
    setState((s) => {
      const recipe = typeof next === "function" ? next(s.recipe) : next;
      return stableJson(recipe) === stableJson(s.recipe) ? s : { ...s, recipe };
    });
  }, []);
  const setRunId = useCallback((id: string | null) => {
    setState((s) => ({ ...s, runId: id, runRecipe: id ? stableJson(s.recipe) : null }));
  }, []);
  const reset = useCallback(() => setState(fresh()), []);

  const value = useMemo<RunContextValue>(() => ({
    recipe: state.recipe, setRecipe, runId: state.runId, setRunId, reset,
    recipeChangedSinceRun: state.runId !== null && state.runRecipe !== null && state.runRecipe !== stableJson(state.recipe),
  }), [state, setRecipe, setRunId, reset]);

  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

export function useRunContext(): RunContextValue {
  const ctx = useContext(RunContext);
  if (!ctx) throw new Error("useRunContext needs a <RunProvider> above it");
  return ctx;
}

/** The current recipe, like useState: `const [recipe, setRecipe] = useRecipe()`. The setter takes a value or an updater. */
export function useRecipe(): [Recipe, Dispatch<SetStateAction<Recipe>>] {
  const { recipe, setRecipe } = useRunContext();
  return [recipe, setRecipe];
}

/** The current run id, like useState: `const [runId, setRunId] = useRunId()`. `null` means no run yet. */
export function useRunId(): [string | null, (id: string | null) => void] {
  const { runId, setRunId } = useRunContext();
  return [runId, setRunId];
}
