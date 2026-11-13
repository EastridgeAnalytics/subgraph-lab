"""The recipe file: one loop, saved. The app writes it; the CLI, the API and the agent run it."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from lab.contracts.graph import GraphMapping


class PullSpec(BaseModel):
    """The broad slice pulled from the master graph into a fresh run store."""

    mode: Literal["schema", "cypher"] = "schema"
    labels: list[str] = Field(default_factory=list)  # schema mode; empty means every label in the mapping
    types: list[str] = Field(default_factory=list)
    since: str | None = None  # ISO-8601, applied to the payment time
    until: str | None = None
    query: str | None = None  # cypher mode; must RETURN graph elements
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cypher_needs_query(self) -> PullSpec:
        if self.mode == "cypher" and not self.query:
            raise ValueError("a cypher pull needs a query")
        return self


class SearchSpec(BaseModel):
    """A search-defined slice inside the run store (page 02). Give text, an account key, or both."""

    text: str | None = None
    account: str | None = None
    k: int | None = None  # None means the LAB_SEARCH_K setting
    hops: int | None = None  # None means LAB_SEARCH_HOPS

    @model_validator(mode="after")
    def _needs_something(self) -> SearchSpec:
        if not self.text and not self.account:
            raise ValueError("a search slice needs text, an account key, or both")
        return self


class WriteBack(BaseModel):
    accounts: bool = True
    alerts: bool = True
    communities: bool = True
    vectors: bool = True
    dispositions: bool = True


ModelName = Literal["node2vec", "tgn", "communities", "velocity"]


class Recipe(BaseModel):
    name: str = "default"
    graph: GraphMapping = Field(default_factory=GraphMapping)
    pull: PullSpec = Field(default_factory=PullSpec)
    search: SearchSpec | None = None
    models: list[ModelName] = Field(default_factory=lambda: ["node2vec", "tgn", "communities", "velocity"])
    factors: list[str] | None = None  # decision 40: the factor ids this run scores with; None means every factor
    factors_file: str | None = None  # None means LAB_FACTORS_FILE
    policy_file: str | None = None
    summaries: bool = True
    write_back: WriteBack = Field(default_factory=WriteBack)

    @classmethod
    def load(cls, path: Path) -> Recipe:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def dump_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json", exclude_none=True), sort_keys=False)
