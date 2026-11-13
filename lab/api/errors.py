"""Errors the routes and the MCP tools both say the same way: a status and one plain sentence.

Routes answer `{"detail": "<sentence>"}` with the status. Tools raise the same sentence as a tool error. A run that
does not exist is 404, a run id of the wrong shape is 400, a run that is busy or not far enough along is 409, the
master graph or `relate` failing is 502, and anything unforeseen is 500 with the exception's type and message.
"""
from __future__ import annotations

from lab.common.log import get_logger

log = get_logger("api")


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class BadRunId(ApiError):
    def __init__(self, detail: str):
        super().__init__(400, detail)


class NotFound(ApiError):
    def __init__(self, detail: str):
        super().__init__(404, detail)


class NotReady(ApiError):
    """The run exists but is busy, or has not got far enough for this."""

    def __init__(self, detail: str):
        super().__init__(409, detail)


def as_api_error(exc: Exception) -> ApiError:
    """The `ApiError` for anything a route or tool body can raise. Unforeseen exceptions are logged once."""
    from neo4j.exceptions import DriverError, Neo4jError

    from lab.api.worker import RunBusy
    from lab.common.relate_cli import RelateError
    from lab.roundtrip.delta import DeltaError
    from lab.roundtrip.pull import PullError
    from lab.roundtrip.push import PushError, PushNotConfirmed
    from lab.roundtrip.stage import NothingStaged, StagedError
    from lab.slice.search import FirstPassNotRun, SliceOverCap

    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, PushNotConfirmed):
        return ApiError(400, str(exc))
    if isinstance(exc, RunBusy | NothingStaged | FirstPassNotRun | SliceOverCap | PullError):
        return ApiError(409, str(exc))
    if isinstance(exc, StagedError):
        return ApiError(422, str(exc))
    if isinstance(exc, DeltaError | PushError | RelateError):
        return ApiError(502, str(exc))
    if isinstance(exc, Neo4jError | DriverError):
        return ApiError(502, f"the master graph said: {type(exc).__name__}: {str(exc)[:300]}")
    if isinstance(exc, FileNotFoundError) and str(exc).startswith("relate binary"):
        return ApiError(503, str(exc))
    if isinstance(exc, ValueError):
        return ApiError(422, str(exc)[:500])
    log.exception("unforeseen error in an API call")
    return ApiError(500, f"{type(exc).__name__}: {str(exc)[:400]}")
