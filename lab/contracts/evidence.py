"""The evidence object behind every summary, and the summary itself (page 05).

Evidence carries keys and totals, never names. It is checked against this schema and hashed before anything reads
it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lab.common.ids import evidence_hash


class SharedIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    key: str
    members_sharing: int


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inflow: float
    outflow: float
    internal: float  # paid between members
    cycles: int
    first_at: str | None = None
    last_at: str | None = None
    span_hours: float | None = None


class FactorHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor_id: str
    name: str
    accounts: int
    evidence_ids: list[str] = Field(default_factory=list)


class MemberScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    score: float


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    communities_in_slice: int
    score_rank: int  # 1 is the highest score in the slice
    median_size: float
    median_score: float


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: Literal["community", "alert"]
    key: str
    run_id: str
    member_count: int
    member_keys: list[str]
    score: float
    score_parts: dict[str, float]
    shared_identifiers: list[SharedIdentifier]
    money: Money
    factors: list[FactorHit]
    top_members: list[MemberScore]
    comparison: Comparison

    def hash(self) -> str:
        return evidence_hash(self.model_dump(mode="json"))


SummarySource = Literal["api", "agent", "template"]


class Summary(BaseModel):
    key: str
    text: str
    source: SummarySource
    model: str | None = None
    evidence_hash: str
    checks: dict[str, bool] = Field(default_factory=dict)
    failed_reason: str | None = None  # set when a writer's text failed a check and the template replaced it
