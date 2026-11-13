"""The REST routes: paths, request and response shapes. The app and the MCP tools both sit on these."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from lab.contracts.evidence import Evidence, Summary
from lab.contracts.factors import Factor
from lab.contracts.graph import Disposition
from lab.contracts.manifest import PushEntity, RunManifest
from lab.contracts.recipe import PullSpec, Recipe, SearchSpec

# method, path, request model, response model
ROUTES: list[tuple[str, str, str | None, str]] = [
    ("GET", "/api/health", None, "Health"),
    ("POST", "/api/connect/test", "ConnectTest", "ConnectResult"),
    ("POST", "/api/connect/use", "ConnectTest", "ConnectResult"),
    ("GET", "/api/connect", None, "ConnectResult"),
    ("POST", "/api/slice/estimate", "SliceRequest", "SliceEstimate"),
    ("POST", "/api/slice/run", "SliceRequest", "RunStarted"),
    ("GET", "/api/models", None, "ModelsList"),
    ("POST", "/api/runs", "RunRequest", "RunStarted"),
    ("GET", "/api/runs", None, "RunsList"),
    ("GET", "/api/runs/{run_id}", None, "RunManifest"),
    ("POST", "/api/runs/{run_id}/resume", None, "RunStarted"),
    ("GET", "/api/runs/{run_id}/results/accounts", None, "AccountsPage"),
    ("GET", "/api/runs/{run_id}/results/communities", None, "CommunitiesPage"),
    ("GET", "/api/runs/{run_id}/results/community/{key}", None, "CommunityDetail"),
    ("GET", "/api/runs/{run_id}/review", None, "Review"),
    ("POST", "/api/runs/{run_id}/push", "PushRequest", "PushResult"),
    ("POST", "/api/runs/{run_id}/dispositions", "DispositionRequest", "DispositionResult"),
    ("GET", "/api/runs/{run_id}/timeline", None, "Timeline"),
    ("GET", "/api/runs/{run_id}/code", None, "ShowCode"),
]


class Health(BaseModel):
    ok: bool
    version: str
    relate: str


class ConnectTest(BaseModel):
    uri: str | None = None  # None means the settings
    username: str | None = None
    password: str | None = None
    database: str | None = None


class ConnectResult(BaseModel):
    ok: bool
    neo4j_version: str | None = None
    edition: str | None = None
    query_uri: str | None = None
    pull_route: Literal["relate", "bolt"] | None = None
    message: str


class SliceRequest(BaseModel):
    """A search estimate reads a run store whose first pass is done (decision 31): `run_id`, or else the newest such
    run. With neither, the estimate says a schema slice must be pulled first."""

    run_id: str | None = None
    recipe: Recipe = Field(default_factory=Recipe)
    pull: PullSpec | None = None  # overrides recipe.pull
    search: SearchSpec | None = None  # overrides recipe.search


class Hit(BaseModel):
    key: str
    rrf: float
    found_by: list[Literal["vector", "fulltext"]]


class SliceEstimate(BaseModel):
    mode: Literal["schema", "cypher", "search"]
    hits: list[Hit] = Field(default_factory=list)
    hops: int | None = None
    nodes: int
    relationships: int
    memory_bytes: int  # from Relate's documented bytes per node and relationship, times the served factor
    over_cap: bool
    cap: int
    note: str | None = None


class RunRequest(BaseModel):
    recipe: Recipe = Field(default_factory=Recipe)


class RunStarted(BaseModel):
    run_id: str


class RunsList(BaseModel):
    runs: list[RunManifest]


class ModelInfo(BaseModel):
    name: str
    kind: Literal["model", "factor"]
    description: str
    code_path: str
    factor: Factor | None = None


class ModelsList(BaseModel):
    models: list[ModelInfo]
    factor_version: str
    policy: dict


class AccountRow(BaseModel):
    key: str
    risk_score: float
    risk_score_model: float | None = None
    risk_score_rules: float | None = None
    factors: list[dict]
    community_key: str | None = None
    alert_key: str | None = None
    disposition: Disposition | None = None


class AccountsPage(BaseModel):
    total: int
    rows: list[AccountRow]


class CommunityRow(BaseModel):
    key: str
    size: int
    score: float
    summary: str | None = None
    summary_source: str | None = None
    disposition: Disposition | None = None


class CommunitiesPage(BaseModel):
    total: int
    rows: list[CommunityRow]


class GraphNode(BaseModel):
    id: str
    label: str
    props: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    props: dict = Field(default_factory=dict)


class CommunityDetail(BaseModel):
    row: CommunityRow
    score_parts: dict[str, float]
    evidence: Evidence
    summary: Summary | None = None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    similar: list[CommunityRow]  # "communities like this one"


class Delta(BaseModel):
    new_alerts: int
    new_communities: int
    accounts_scored: int
    score_changes: dict[str, int]  # bucket ("0.0-0.1", ...) -> accounts whose score moved by that much
    missing_remote_keys: int  # accounts in the push store not found on the master graph, from the sample
    sampled: int
    vectors: int
    dispositions: int
    bytes_estimate: int


class Review(BaseModel):
    run_id: str
    plan: list[PushEntity]  # the dry run, parsed
    plan_text: str  # the dry run as relate printed it
    delta: Delta


class PushRequest(BaseModel):
    confirm: bool = False


class PushResult(BaseModel):
    run_id: str
    pushed: bool
    entities: list[PushEntity]
    resumed: bool = False
    message: str


class DispositionRequest(BaseModel):
    kind: Literal["account", "alert", "community"]
    key: str
    disposition: Disposition
    by: str


class DispositionResult(BaseModel):
    ok: bool
    staged: bool  # true: it will travel with the next push


class TimelinePoint(BaseModel):
    at: str
    accounts: int
    payments: int
    amount: float


class Timeline(BaseModel):
    start: str
    end: str
    as_of: str | None = None
    points: list[TimelinePoint]


class ShowCode(BaseModel):
    recipe_yaml: str
    python: str
