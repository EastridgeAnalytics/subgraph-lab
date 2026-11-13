// "Show code": a button that opens a side panel with the recipe as YAML and the Python that runs it (page 06, the
// fourth rule for every screen). It sits in the app header, so every screen has it. With a run id it shows what the
// lab says that run was (`api.code`); without one, or once the recipe was edited after the run, it builds both from
// the current recipe in the browser.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import type { Recipe, ShowCode as ShowCodeData } from "../api/types";
import { useRunContext } from "../state/run";
import { recipeToPython, recipeToYaml } from "./code";
import { errorMessage } from "./format";
import { Button, Notice, Tabs } from "./ui";

const TABS = [{ id: "yaml", label: "Recipe (YAML)" }, { id: "python", label: "Python" }];
const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function ShowCode({ runId: runIdProp, recipe: recipeProp }: {
  /** Override the run id from the context. `null` forces the in-browser code. */
  runId?: string | null;
  /** Override the recipe from the context. */
  recipe?: Recipe;
}) {
  const ctx = useRunContext();
  const recipe = recipeProp ?? ctx.recipe;
  const runId = runIdProp === undefined ? ctx.runId : runIdProp;
  const fromRun = runId !== null && !(runIdProp === undefined && ctx.recipeChangedSinceRun);

  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("yaml");
  const [remote, setRemote] = useState<ShowCodeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout>>();
  const titleId = useId();

  const local = useMemo<ShowCodeData>(
    () => ({ recipe_yaml: recipeToYaml(recipe), python: recipeToPython(recipe) }), [recipe]);

  // The lab's own answer for a run, fetched each time the panel opens.
  useEffect(() => {
    if (!open || !fromRun || runId === null) return;
    let live = true;
    setLoading(true); setError(null); setRemote(null);
    api.code(runId)
      .then((c) => { if (live) setRemote(c); })
      .catch((e) => { if (live) setError(errorMessage(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [open, fromRun, runId]);

  // Focus goes into the panel, Escape closes it, Tab stays inside it, and focus returns to the button.
  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); return; }
      if (e.key !== "Tab" || !panelRef.current) return;
      const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.hasAttribute("disabled"));
      if (items.length === 0) return;
      const first = items[0], last = items[items.length - 1];
      const inside = panelRef.current.contains(document.activeElement);
      if (!inside || (e.shiftKey && document.activeElement === first)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); trigger?.focus(); };
  }, [open]);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  const shown: ShowCodeData | null = fromRun && !error ? remote : local;
  const text = shown ? (tab === "yaml" ? shown.recipe_yaml : shown.python) : "";

  const onCopy = async () => {
    clearTimeout(copyTimer.current);
    try {
      await navigator.clipboard.writeText(text);
      setCopy("copied");
    } catch {
      setCopy("failed");
    }
    copyTimer.current = setTimeout(() => setCopy("idle"), 2000);
  };

  return (
    <>
      <Button ref={triggerRef} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>Show code</Button>
      {open && createPortal(
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-slate-900/30" aria-hidden="true" onClick={() => setOpen(false)} />
          <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={titleId}
            className="absolute inset-y-0 right-0 flex w-full max-w-2xl flex-col gap-4 overflow-y-auto bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 id={titleId} className="text-lg font-semibold text-slate-900">The code behind this</h2>
                <p className="mt-1 text-sm text-slate-600">
                  {fromRun && runId
                    ? <>The recipe and the Python for run <code className="text-slate-800">{runId}</code>.</>
                    : "Built in your browser from the recipe as it stands now. Nothing has run from it yet."}
                </p>
              </div>
              <Button ref={closeRef} onClick={() => setOpen(false)}>Close</Button>
            </div>

            {runIdProp === undefined && ctx.runId !== null && ctx.recipeChangedSinceRun && (
              <Notice kind="info">The recipe has changed since run {ctx.runId} started, so this is the code for the recipe as it stands now.</Notice>
            )}
            {error && <Notice kind="warning" title="The lab could not give this run's code">
              {error}. Showing the code built from the current recipe instead.</Notice>}

            <Tabs tabs={TABS} value={tab} onChange={(t) => { setTab(t); setCopy("idle"); }} label="Code">
              {loading ? <p role="status" className="text-sm text-slate-600">Loading the code…</p> : (
                <div className="space-y-3">
                  <div className="flex items-center justify-end gap-3">
                    <span role="status" className="text-sm text-slate-600">
                      {copy === "copied" ? "Copied to the clipboard." : copy === "failed" ? "Could not copy. Select the text and copy it." : ""}
                    </span>
                    <Button onClick={onCopy} disabled={!shown}>{copy === "copied" ? "Copied" : "Copy"}</Button>
                  </div>
                  <pre tabIndex={0} aria-label={tab === "yaml" ? "Recipe as YAML" : "Python"}
                    className="max-h-[70vh] overflow-auto rounded-md border border-slate-200 bg-slate-50 p-4 text-sm leading-relaxed text-slate-800">
                    <code>{text}</code>
                  </pre>
                </div>
              )}
            </Tabs>
          </div>
        </div>,
        document.body)}
    </>
  );
}
