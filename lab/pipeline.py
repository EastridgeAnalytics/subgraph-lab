"""One run of the loop, step by step, stopping before the push (page 00, "The loop").

`run_recipe(recipe)` creates a run and runs it; `resume(run)` carries on from the first step that is not done. The
API runs these in a worker thread and the app follows the manifest. The push is never a step here: it waits for a
person, through `apply_push(confirm=True)`.

| Step | Runs when | Calls |
|---|---|---|
| pull | the run store does not exist yet | `run.pull()` |
| first_pass | the recipe searches, or lists node2vec | `run.first_pass()` |
| slice | the recipe has a search | `run.search()` |
| models | the recipe lists tgn | `examples.tgn.run_model(run, keys)` |
| score | always | `run.score()` (factors, policy, communities, summaries, staging) |
| review | always | `run.preview_push()` |
"""
from __future__ import annotations

import importlib
from pathlib import Path

from lab.common.log import get_logger
from lab.contracts.manifest import StepStatus
from lab.contracts.recipe import Recipe
from lab.runs import Run

log = get_logger("pipeline")

STEPS = ("pull", "first_pass", "slice", "models", "score", "review")


def wanted(run: Run) -> list[str]:
    """The steps this run's recipe asks for, in order."""
    r = run.recipe
    steps = ["pull"]
    if r.search or "node2vec" in r.models:
        steps.append("first_pass")
    if r.search:
        steps.append("slice")
    if "tgn" in r.models:
        steps.append("models")
    steps += ["score", "review"]
    return steps


def _models(run: Run) -> dict[str, int]:
    from lab.slice.search import current_slice

    run.manifest.start("models")
    run.save()
    keys = current_slice(run)
    counts: dict[str, int] = {}
    if "tgn" in run.recipe.models:
        module = importlib.import_module("examples.tgn")
        counts["tgn_accounts"] = int(module.run_model(run, keys))
    run.manifest.finish("models", counts)
    run.save()
    return counts


def _do(run: Run, step: str) -> None:
    log.info("run %s: %s", run.id, step)
    if step == "pull":
        run.pull()
    elif step == "first_pass":
        run.first_pass()
    elif step == "slice":
        run.search()
    elif step == "models":
        _models(run)
    elif step == "score":
        run.score()
    elif step == "review":
        run.preview_push()


def resume(run: Run) -> Run:
    """Run every wanted step that is not done, in order. A failure is on the manifest; it is raised too."""
    for step in wanted(run):
        run.reload()
        if run.manifest.step(step).status is StepStatus.DONE:
            continue
        if step == "pull" and run.paths.store.exists():
            from lab.roundtrip.pull import _reset_store

            _reset_store(run)  # a pull that did not finish leaves a store a second pull would refuse
        try:
            _do(run, step)
        except Exception as e:
            run.reload()
            if run.manifest.step(step).status is not StepStatus.FAILED:
                run.manifest.fail(step, f"{type(e).__name__}: {e}"[:500])
                run.save()
            raise
    for step in STEPS:
        if step not in wanted(run) and run.manifest.step(step).status is StepStatus.PENDING:
            run.manifest.step(step).status = StepStatus.SKIPPED
            run.manifest.step(step).note = "the recipe does not ask for it"
    run.manifest.status = StepStatus.DONE  # the loop is done up to the review; the push waits for a person
    run.save()
    return run


def run_recipe(recipe: Recipe, data_dir: Path | None = None, run_id: str | None = None) -> Run:
    """Create a run for `recipe` and run it up to the review."""
    run = Run.create(recipe, data_dir=data_dir, run_id=run_id)
    return resume(run)
