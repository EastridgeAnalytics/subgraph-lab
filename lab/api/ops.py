"""What the routes and the MCP tools do. Each is thin: check, call the lab's own function, shape the answer.

The functions that do the work are elsewhere (`lab.pipeline`, `lab.slice`, `lab.roundtrip`, `lab.scoring`); nothing
here is a second version of any of them. Routes and tools both come through here, so a person and an agent get the
same answer and the same refusal.

Rules kept here:
- A run id is checked against the id pattern before it touches a path.
- Work that writes to a run (`push`, `review`, a disposition, a search, `score`) claims the run with
  `worker.exclusive` first, and only then reads its manifest, so it never runs beside the worker or another
  request on the same run. Work that only reads takes a `RunView`, which never opens the manifest (`view.py`).
- `push` refuses without `confirm=True` before it opens anything. `apply` refuses again, as its first act.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lab.api import results
from lab.api.connection import ConnectionState
from lab.api.errors import ApiError, BadRunId, NotFound, NotReady
from lab.api.relate_server import RelateServer
from lab.api.view import RunView, keep_saving, read_manifest
from lab.api.worker import Worker
from lab.common.store import list_runs, run_paths
from lab.contracts.api import (
    DispositionRequest,
    DispositionResult,
    PushResult,
    Review,
    RunStarted,
    SliceEstimate,
    SliceRequest,
)
from lab.contracts.manifest import RunManifest, StepStatus
from lab.contracts.recipe import Recipe
from lab.contracts.tools import (
    DescribeIn,
    DescribeOut,
    EgressIn,
    EgressOut,
    ExplainOut,
    GraphDiffIn,
    GraphDiffOut,
    ScoreOut,
    SearchSliceIn,
)
from lab.runs import Run

RUN_ID = re.compile(r"^run-\d{8}T\d{6}Z-[0-9a-z]{1,16}$")  # lab.common.ids.run_id: run-20261112T214500Z-1a2b3c


@dataclass
class LabState:
    """What one running lab holds: the worker, the Relate server it supervises, and the session's connection."""

    worker: Worker
    relate: RelateServer | None = None
    connection: ConnectionState = field(default_factory=ConnectionState)


# ---- runs ------------------------------------------------------------------------------------------------------
def check_run_id(run_id: str) -> str:
    """The id if it has the shape of a run id, else a 400. Nothing else touches a path before this."""
    if not RUN_ID.fullmatch(run_id or ""):
        raise BadRunId("a run id looks like run-20261112T214500Z-1a2b3c")
    return run_id


def load_run(run_id: str) -> Run:
    """The run with its manifest, for work that writes to it. Claim it (`worker.exclusive`) before calling this."""
    check_run_id(run_id)
    try:
        return keep_saving(Run(run_id))
    except FileNotFoundError as e:
        raise NotFound(f"no run {run_id}") from e
    except (OSError, ValueError) as e:  # a manifest or recipe on disk that cannot be read is the lab's problem
        raise ApiError(500, f"run {run_id} cannot be read: {type(e).__name__}: {str(e)[:200]}") from e


def view_run(run_id: str) -> RunView:
    """The run as a read-only route needs it: no manifest is opened."""
    check_run_id(run_id)
    try:
        return RunView(run_id)
    except FileNotFoundError as e:
        raise NotFound(f"no run {run_id}") from e
    except (OSError, ValueError) as e:
        raise ApiError(500, f"run {run_id} cannot be read: {type(e).__name__}: {str(e)[:200]}") from e


def load_manifest(run_id: str) -> RunManifest:
    """The manifest as saved, read so that it never blocks the worker's next save."""
    check_run_id(run_id)
    path = run_paths(run_id).manifest
    if not path.exists():
        raise NotFound(f"no run {run_id}")
    try:
        return read_manifest(path)
    except (OSError, ValueError) as e:
        raise ApiError(500, f"the manifest of {run_id} cannot be read: {type(e).__name__}: {str(e)[:200]}") from e


def list_manifests() -> list[RunManifest]:
    out = []
    for run_id in list_runs():  # newest first
        try:
            out.append(load_manifest(run_id))
        except Exception:  # noqa: BLE001 (a run that cannot be read is left out of the list, not the whole list)
            continue
    return out


def start_run(state: LabState, recipe: Recipe) -> RunStarted:
    """Create the run and queue it: it returns before anything runs."""
    run = Run.create(recipe)
    state.worker.submit(run.id)
    return RunStarted(run_id=run.id)


def slice_run(state: LabState, request: SliceRequest) -> RunStarted:
    """The Slice screen's run: its recipe, with the request's `pull` and `search` laid over it. Always a new run; a
    search slices that run's own store after its first pass (decision 31)."""
    update = {}
    if request.pull is not None:
        update["pull"] = request.pull
    if request.search is not None:
        update["search"] = request.search
    return start_run(state, request.recipe.model_copy(update=update))


def resume_run(state: LabState, run_id: str) -> RunStarted:
    view_run(run_id)
    state.worker.submit(run_id)  # already queued or running is fine: it carries on either way
    return RunStarted(run_id=run_id)


# ---- the slice ---------------------------------------------------------------------------------------------------
def newest_with_first_pass() -> str | None:
    """The newest run whose first pass is done: the run a search slices when the request names none (decision 31)."""
    for run_id in list_runs():  # newest first
        try:
            if read_manifest(run_paths(run_id).manifest).step("first_pass").status is StepStatus.DONE:
                return run_id
        except Exception:  # noqa: BLE001
            continue
    return None


def slice_estimate(state: LabState, request: SliceRequest) -> SliceEstimate:
    """`lab.slice.estimate.estimate`. A search slices a run store and records itself on that run's manifest, so the run
    it will use is named first and claimed, and the estimate is asked for that run."""
    from lab.slice.estimate import estimate

    if (request.search or request.recipe.search) is None:
        return estimate(request)  # a pull estimate reads the master graph only
    run_id = check_run_id(request.run_id) if request.run_id else newest_with_first_pass()
    if run_id is None:
        return estimate(request)  # no run to search yet: it says to pull a schema slice first
    with state.worker.exclusive(run_id):
        run = load_run(run_id)
        try:
            return estimate(request.model_copy(update={"run_id": run_id}))
        finally:
            run.close()


# ---- review and push -------------------------------------------------------------------------------------------
def review(state: LabState, run_id: str) -> Review:
    check_run_id(run_id)
    with state.worker.exclusive(run_id):
        run = load_run(run_id)
        try:
            return run.preview_push()
        finally:
            run.close()


def push(state: LabState, run_id: str, confirm: bool) -> PushResult:
    """`apply(run, confirm)`, the only writer to the master graph. Without `confirm` it refuses first, writes nothing."""
    from lab.roundtrip.push import PushNotConfirmed

    if confirm is not True:
        raise PushNotConfirmed(
            f"push of {run_id} not confirmed: the push needs confirm=true after a person has seen the review. "
            "Nothing was sent."
        )
    check_run_id(run_id)
    with state.worker.exclusive(run_id):
        run = load_run(run_id)
        try:
            require_fresh_review(run)
            return run.apply_push(confirm=True)
        finally:
            run.close()


def require_fresh_review(run) -> None:
    """Decision 37 (A2): a push follows a review someone has seen, and nothing was staged after it."""
    from lab.contracts.manifest import StepStatus

    review, stage = run.manifest.step("review"), run.manifest.step("stage")
    if review.status is not StepStatus.DONE or not review.finished_at:
        raise ApiError(409, "open the review first: a push follows a dry run someone has seen. Nothing was sent.")
    if stage.finished_at and stage.finished_at > review.finished_at:
        raise ApiError(409, "something was staged after the review: open the review again before pushing. "
                            "Nothing was sent.")


def disposition(state: LabState, run_id: str, request: DispositionRequest) -> DispositionResult:
    from lab.roundtrip.stage import stage_disposition

    if not request.by.strip():
        raise ApiError(422, "a disposition records who made it: `by` is empty")
    check_run_id(run_id)
    with state.worker.exclusive(run_id):
        run = load_run(run_id)
        try:
            stage_disposition(run, request.kind, request.key, request.disposition, request.by)
        except ValueError as e:
            if str(e).startswith("no staged"):
                raise NotFound(str(e)) from e
            raise
        finally:
            run.close()
    return DispositionResult(ok=True, staged=True)


# ---- the tools that act on a run ---------------------------------------------------------------------------------
def egress(state: LabState, request: EgressIn) -> EgressOut:
    """A fresh run, pulled now: what the `egress` tool does. The REST routes queue the whole run instead.

    When the recipe asks for the first pass (it lists node2vec, or has a search) the first pass runs too: no tool runs it,
    and `search_slice` needs it (decision 31).
    """
    from lab.pipeline import wanted

    recipe = request.recipe if request.pull is None else request.recipe.model_copy(update={"pull": request.pull})
    run = keep_saving(Run.create(recipe))
    until = "first_pass" if "first_pass" in wanted(run) else "pull"
    run.close()
    state.worker.submit(run.id, until=until)  # decision 38: queued, answered at once; run_status follows it
    return EgressOut(run_id=run.id, status="queued")


def search_slice(state: LabState, request: SearchSliceIn) -> SliceEstimate:
    from lab.slice.search import search_slice as search

    check_run_id(request.run_id)
    with state.worker.exclusive(request.run_id):
        run = load_run(request.run_id)
        try:
            return search(run, request.search)
        finally:
            run.close()


def score(state: LabState, run_id: str) -> ScoreOut:
    check_run_id(run_id)
    with state.worker.exclusive(run_id):
        run = load_run(run_id)
        try:
            if run.manifest.step("pull").status is not StepStatus.DONE:
                raise NotReady(f"run {run_id} has not been pulled: run egress first")
            manifest = run.manifest
        finally:
            run.close()
    state.worker.submit(run_id, until="score")  # decision 38: queued, answered at once; run_status follows it
    return ScoreOut(run_id=run_id, status="queued", manifest=manifest)


def run_status(run_id: str, wait_seconds: int = 0) -> RunManifest:
    """The manifest: what `run_status` returns while an agent waits on egress or score. With `wait_seconds`, it holds
    the answer until the run is no longer queued or running, or the time is up."""
    import time

    check_run_id(run_id)
    path = run_paths(run_id).manifest
    if not path.exists():
        raise NotFound(f"no run {run_id}")
    end = time.monotonic() + wait_seconds
    manifest = read_manifest(path)
    while manifest.status in (StepStatus.PENDING, StepStatus.RUNNING) and time.monotonic() < end:
        time.sleep(1.0)
        manifest = read_manifest(path)
    return manifest


def explain(run_id: str, key: str) -> ExplainOut:
    return results.explain(view_run(run_id), key)


def describe(state: LabState, request: DescribeIn) -> DescribeOut:
    """Store a summary an agent wrote, after the summaries' own checks. Those are track 3b's; until they exist this says
    so and stores nothing, rather than storing text no check has seen."""
    import importlib

    view_run(request.run_id)
    try:
        summaries = importlib.import_module("lab.summaries")
    except ModuleNotFoundError:  # pragma: no cover (the package is in the tree; its contents are what is missing)
        summaries = None
    hook = getattr(summaries, "describe", None)
    if hook is None:
        raise NotReady(
            "describe_alert is not available yet: it stores a summary only after the checks on numbers, members and "
            "wording, and those arrive with the summaries step (track 3b). Nothing was stored."
        )
    with state.worker.exclusive(request.run_id):
        run = load_run(request.run_id)
        try:
            return hook(run, request.key, request.text)
        finally:
            run.close()


def graph_diff(request: GraphDiffIn) -> GraphDiffOut:
    return results.graph_diff(view_run(request.run_a), view_run(request.run_b))
