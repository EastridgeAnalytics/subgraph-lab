"""lab/pipeline.py: one run of the loop, step by step, stopping before the push."""
from __future__ import annotations

import pytest

from lab.contracts.manifest import StepStatus
from lab.contracts.recipe import PullSpec, Recipe, SearchSpec
from lab.pipeline import resume, run_recipe, wanted
from lab.runs import Run


def test_wanted_follows_the_recipe(fixture_run):
    fixture_run.recipe = Recipe(models=["communities"])
    assert wanted(fixture_run) == ["pull", "score", "review"]
    fixture_run.recipe = Recipe(search=SearchSpec(text="new account"), models=["node2vec", "tgn"])
    assert wanted(fixture_run) == ["pull", "first_pass", "slice", "models", "score", "review"]


def test_resume_runs_the_rest_and_never_pushes(fixture_run, monkeypatch):
    run: Run = fixture_run
    run.recipe = Recipe(search=SearchSpec(text="money in and out within a day across new accounts", k=10, hops=1),
                        models=["node2vec", "communities"])
    run.manifest.finish("pull", {"nodes": 1})  # the fixture store stands in for a pull
    run.save()
    from lab.roundtrip.indexes import create_indexes

    create_indexes(run.store(), mapping=run.recipe.graph)
    seen: list[str] = []
    monkeypatch.setattr(Run, "preview_push", lambda self: seen.append("review") or self.manifest.finish("review"))
    monkeypatch.setattr(Run, "apply_push", lambda self, confirm=False: pytest.fail("the pipeline pushed"))
    resume(run)
    run.reload()
    for step in ("first_pass", "slice", "score"):
        assert run.manifest.step(step).status is StepStatus.DONE, step
    assert seen == ["review"]
    assert run.manifest.step("models").status is StepStatus.SKIPPED
    assert run.manifest.step("push").status is StepStatus.PENDING


def test_a_failed_step_is_on_the_manifest_and_raised(fixture_run, monkeypatch):
    run: Run = fixture_run
    run.recipe = Recipe(models=["communities"])
    run.manifest.finish("pull", {})
    run.save()
    monkeypatch.setattr(Run, "score", lambda self: (_ for _ in ()).throw(RuntimeError("factor file broken")))
    with pytest.raises(RuntimeError):
        resume(run)
    run.reload()
    assert run.manifest.step("score").status is StepStatus.FAILED
    assert "factor file broken" in run.manifest.step("score").note


@pytest.mark.live
def test_a_recipe_runs_end_to_end_up_to_the_review(live_neo4j, data_dir):
    recipe = Recipe(pull=PullSpec(), search=SearchSpec(text="money in and out within a day across new accounts",
                                                       k=10, hops=1), models=["node2vec", "communities"])
    run = run_recipe(recipe, data_dir=data_dir)
    run.reload()
    for step in ("pull", "first_pass", "slice", "score", "review"):
        assert run.manifest.step(step).status is StepStatus.DONE, step
    assert run.manifest.step("push").status is StepStatus.PENDING
    run.close()
