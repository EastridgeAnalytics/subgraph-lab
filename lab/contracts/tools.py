"""The lab's eight MCP tools (page 06): names, inputs, outputs.

Only `apply_push` with `confirm=true` writes to the master graph. `describe_alert` stores a summary only after the
same checks the API's summaries pass.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from lab.contracts.api import CommunityDetail, PushResult, Review, SliceEstimate
from lab.contracts.evidence import Evidence, Summary
from lab.contracts.manifest import RunManifest
from lab.contracts.recipe import PullSpec, Recipe, SearchSpec


class EgressIn(BaseModel):
    recipe: Recipe = Field(default_factory=Recipe)
    pull: PullSpec | None = None


class EgressOut(BaseModel):
    run_id: str
    nodes: int
    relationships: int
    route: Literal["relate", "bolt"]


class SearchSliceIn(BaseModel):
    run_id: str
    search: SearchSpec


class ScoreIn(BaseModel):
    run_id: str


class ScoreOut(BaseModel):
    run_id: str
    accounts_scored: int
    alerts: int
    communities: int
    manifest: RunManifest


class RunIn(BaseModel):
    run_id: str


class ApplyPushIn(BaseModel):
    run_id: str
    confirm: bool = False


class SubjectIn(BaseModel):
    run_id: str
    key: str  # an account key, an alert key, or a community key


class ExplainOut(BaseModel):
    evidence: Evidence
    summary: Summary | None = None


class DescribeIn(BaseModel):
    run_id: str
    key: str
    text: str


class DescribeOut(BaseModel):
    stored: bool
    summary: Summary  # the template when the text failed a check; failed_reason says why


class GraphDiffIn(BaseModel):
    run_a: str
    run_b: str


class GraphDiffOut(BaseModel):
    accounts_both: int
    score_changes: dict[str, int]
    alerts_new: int
    alerts_gone: int
    communities_a: int
    communities_b: int


TOOLS: dict[str, tuple[type[BaseModel], type[BaseModel], str]] = {
    "egress": (EgressIn, EgressOut, "Pull a slice from the master graph into a fresh run store."),
    "search_slice": (SearchSliceIn, SliceEstimate, "Hybrid search plus hops in a run store: hits, size, memory."),
    "score": (ScoreIn, ScoreOut, "Run the models, factors, policy, communities and summaries on a run's slice."),
    "preview_push": (RunIn, Review, "The dry-run plan plus the delta. Writes nothing."),
    "apply_push": (ApplyPushIn, PushResult, "Push a run to the master graph. Fails unless confirm is true."),
    "explain_alert": (SubjectIn, ExplainOut, "The evidence object for an account, alert or community."),
    "describe_alert": (DescribeIn, DescribeOut, "Store a summary for an alert or community, after the checks."),
    "graph_diff": (GraphDiffIn, GraphDiffOut, "What changed between two runs."),
}

# CommunityDetail is reachable from explain_alert through the REST route; listed so schema export includes it.
_ = CommunityDetail
