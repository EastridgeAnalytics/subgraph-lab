"""The FastAPI app: every route in `lab.contracts.api.ROUTES`, the lab's MCP tools at `/mcp`, and the built app at `/`.

`create_app()` builds it. With `supervise=True` (what `serve()` passes) its lifespan also starts the worker, which runs
queued runs one at a time, and the Relate server supervisor, which serves the current run's store read-only; both stop
when the app does. Tests build it with `supervise=False` and start only what they need.

Errors come back as JSON `{"detail": ...}`. A run id is checked before it touches a path (400 for the wrong shape, 404
for no such run). Routes are plain `def`s, so FastAPI runs each in its thread pool; the slow ones (estimate, review,
push) do not hold up the others.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

import lab
from lab.api import ops, results
from lab.api.connection import check_connection
from lab.api.errors import ApiError, NotFound, as_api_error
from lab.api.mcp_tools import build_mcp
from lab.api.relate_server import RelateServer, relate_version
from lab.api.worker import Worker
from lab.common.log import get_logger
from lab.contracts.api import (
    AccountsPage,
    CommunitiesPage,
    CommunityDetail,
    ConnectResult,
    ConnectTest,
    DispositionRequest,
    DispositionResult,
    Health,
    ModelsList,
    PushRequest,
    PushResult,
    Review,
    RunRequest,
    RunsList,
    RunStarted,
    ShowCode,
    SliceEstimate,
    SliceRequest,
    Timeline,
)
from lab.contracts.manifest import RunManifest

log = get_logger("api.app")

# The MCP endpoint answers only requests addressed to the loopback host, so a web page cannot reach it by DNS rebinding.
LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Whatever a route raises becomes an `ApiError`, which the app answers as `{"detail": ...}` with its status."""

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            raise as_api_error(e) from e

    return run


def create_app(*, state: ops.LabState | None = None, supervise: bool = True, app_dist: Path | None = None) -> FastAPI:
    if state is None:
        relate = RelateServer()
        state = ops.LabState(worker=Worker(on_idle=relate.refresh), relate=relate)
    mcp = build_mcp(state)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=LOCAL_HOSTS, allowed_origins=LOCAL_ORIGINS),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if supervise:
            state.worker.recover_orphans()
            state.worker.start()
            if state.relate is not None:
                state.relate.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if supervise:
                if state.relate is not None:
                    await anyio.to_thread.run_sync(state.relate.stop)
                await anyio.to_thread.run_sync(state.worker.stop)

    app = FastAPI(title="subgraph-lab", version=lab.__version__, lifespan=lifespan)
    app.state.lab = state

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Where and why, without the values: a rejected body can hold a password.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    # ---- health and connection ---------------------------------------------------------------------------------
    @app.get("/api/health", response_model=Health)
    @guarded
    def health() -> Health:
        found, relate = relate_version()
        return Health(ok=found, version=lab.__version__, relate=relate)

    @app.post("/api/connect/test", response_model=ConnectResult)
    @guarded
    def connect_test(body: ConnectTest) -> ConnectResult:
        return check_connection(body)

    @app.post("/api/connect/use", response_model=ConnectResult)
    @guarded
    def connect_use(body: ConnectTest) -> ConnectResult:
        if state.worker.busy():
            raise ApiError(409, "a run is in progress; switching the master graph now would mix two graphs in one run. "
                                "Wait for it to finish.")
        result = check_connection(body)
        if result.ok:
            state.connection.use(body)
            result = result.model_copy(update={
                "message": f"{result.message} Using these credentials for this session only; nothing is written to disk."})
        return result

    @app.get("/api/connect", response_model=ConnectResult)
    @guarded
    def connect() -> ConnectResult:
        return state.connection.current()

    # ---- the slice and the models ----------------------------------------------------------------------------
    @app.post("/api/slice/estimate", response_model=SliceEstimate)
    @guarded
    def slice_estimate(body: SliceRequest) -> SliceEstimate:
        return ops.slice_estimate(state, body)

    @app.post("/api/slice/run", response_model=RunStarted)
    @guarded
    def slice_run(body: SliceRequest) -> RunStarted:
        return ops.slice_run(state, body)

    @app.get("/api/models", response_model=ModelsList)
    @guarded
    def models() -> ModelsList:
        return results.models_list()

    # ---- runs --------------------------------------------------------------------------------------------------
    @app.post("/api/runs", response_model=RunStarted)
    @guarded
    def start_run(body: RunRequest) -> RunStarted:
        return ops.start_run(state, body.recipe)

    @app.get("/api/runs", response_model=RunsList)
    @guarded
    def runs() -> RunsList:
        return RunsList(runs=ops.list_manifests())

    @app.get("/api/runs/{run_id}", response_model=RunManifest)
    @guarded
    def run(run_id: str) -> RunManifest:
        return ops.load_manifest(run_id)

    @app.post("/api/runs/{run_id}/resume", response_model=RunStarted)
    @guarded
    def resume(run_id: str) -> RunStarted:
        return ops.resume_run(state, run_id)

    # ---- results -----------------------------------------------------------------------------------------------
    @app.get("/api/runs/{run_id}/results/accounts", response_model=AccountsPage)
    @guarded
    def accounts(run_id: str, limit: int = Query(results.DEFAULT_LIMIT, ge=1, le=results.MAX_LIMIT),
                 offset: int = Query(0, ge=0)) -> AccountsPage:
        return results.accounts_page(ops.view_run(run_id), limit, offset)

    @app.get("/api/runs/{run_id}/results/communities", response_model=CommunitiesPage)
    @guarded
    def communities(run_id: str, limit: int = Query(results.DEFAULT_LIMIT, ge=1, le=results.MAX_LIMIT),
                    offset: int = Query(0, ge=0)) -> CommunitiesPage:
        return results.communities_page(ops.view_run(run_id), limit, offset)

    @app.get("/api/runs/{run_id}/results/community/{key}", response_model=CommunityDetail)
    @guarded
    def community(run_id: str, key: str) -> CommunityDetail:
        return results.community_detail(ops.view_run(run_id), key)

    # ---- review, push, decisions -----------------------------------------------------------------------------
    @app.get("/api/runs/{run_id}/review", response_model=Review)
    @guarded
    def review(run_id: str) -> Review:
        return ops.review(state, run_id)

    @app.post("/api/runs/{run_id}/push", response_model=PushResult)
    @guarded
    def push(run_id: str, body: PushRequest) -> PushResult:
        return ops.push(state, run_id, body.confirm)

    @app.post("/api/runs/{run_id}/dispositions", response_model=DispositionResult)
    @guarded
    def dispositions(run_id: str, body: DispositionRequest) -> DispositionResult:
        return ops.disposition(state, run_id, body)

    # ---- timeline and code -----------------------------------------------------------------------------------
    @app.get("/api/runs/{run_id}/timeline", response_model=Timeline)
    @guarded
    def timeline(run_id: str, as_of: str | None = None) -> Timeline:
        return results.timeline(ops.view_run(run_id), as_of)

    @app.get("/api/runs/{run_id}/code", response_model=ShowCode)
    @guarded
    def code(run_id: str) -> ShowCode:
        return results.show_code(ops.view_run(run_id))

    # ---- the MCP tools, then the app (after every API route) -------------------------------------------------
    app.router.routes.extend(mcp_app.routes)
    if app_dist is not None:
        _serve_app(app, app_dist)
    return app


def _serve_app(app: FastAPI, dist: Path) -> None:
    """The built single-page app at `/`: a file that exists is sent as it is, any other path without a file extension
    gets `index.html` so the client router can take it. `/api` and `/mcp` paths that match no route stay 404."""
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        first = path.split("/", 1)[0]
        if first in ("api", "mcp"):
            raise NotFound(f"no such route /{path}")
        target = (root / path).resolve()
        if path and root in target.parents and target.is_file():
            return FileResponse(target)
        if Path(path).suffix:
            raise NotFound(f"no such file /{path}")
        index = root / "index.html"
        if not index.is_file():
            raise NotFound("the app is not built: run `npm run build` in app/")
        return FileResponse(index)
