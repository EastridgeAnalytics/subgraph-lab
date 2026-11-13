"""The factor file and the policy file (page 04).

A factor is one reason an account could be risky, scored 0 to 1 with its evidence. A factor implementation returns
a `FactorResult` table: the account key, a score in [0, 1], and an evidence JSON string per account. Accounts a
factor says nothing about are simply absent.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

FACTOR_RESULT_COLUMNS = ("key", "score", "evidence")


class Factor(BaseModel):
    id: str
    name: str
    kind: Literal["cypher", "python", "model"]
    query: str | None = None  # cypher: a path relative to the factor file, returning key, value, evidence_ids
    function: str | None = None  # python: "module.path:function"
    source: str | None = None  # model: the example that writes it, such as examples/tgn
    threshold: float | None = None
    weight: float = Field(ge=0)
    description: str | None = None

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError(f"factor id {v!r} must be lower_snake_case")
        return v

    @model_validator(mode="after")
    def _kind_fields(self) -> Factor:
        need = {"cypher": "query", "python": "function", "model": "source"}[self.kind]
        if not getattr(self, need):
            raise ValueError(f"factor {self.id}: a {self.kind} factor needs {need}")
        return self


class FactorFile(BaseModel):
    version: str
    factors: list[Factor]

    @model_validator(mode="after")
    def _unique(self) -> FactorFile:
        ids = [f.id for f in self.factors]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate factor ids: {sorted(dupes)}")
        return self

    @classmethod
    def load(cls, path: Path) -> FactorFile:
        """Accepts the page-04 shape (a list) or {version, factors}. Without a version, the file's hash is it."""
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        if isinstance(raw, list):
            return cls(version=digest, factors=raw)
        return cls(version=str(raw.get("version") or digest), factors=raw.get("factors") or [])


class CommunityPolicy(BaseModel):
    """How a community's score is built from its members and its shape (page 04, level three). Each part is in
    [0, 1]; the score is their weighted mean. A community smaller than `min_size` is not scored or staged."""

    min_size: int = Field(3, ge=2)
    weights: dict[str, float] = Field(default_factory=lambda: {
        "member_risk": 0.35,  # the share of members at or above the alert threshold, and their mean score
        "shared_identifiers": 0.2,  # how many identifiers members share, relative to the community's size
        "cycles": 0.15,  # money that comes back to where it started inside the community
        "new_accounts": 0.15,  # the share of members opened within LAB_NEW_ACCOUNT_DAYS
        "speed": 0.15,  # how fast money moves through: in and out within LAB_PASS_THROUGH_HOURS
    })


class Policy(BaseModel):
    """How factors become account scores.

    risk_score_rules is the weighted mean of the cypher and python factors, risk_score_model the weighted mean of
    the model factors, and risk_score their mean weighted by each side's total weight. A missing factor counts 0.
    An account at or above alert_threshold gets an Alert for the window.
    """

    version: str = "1"
    combine: Literal["weighted_mean"] = "weighted_mean"
    alert_threshold: float = Field(0.6, ge=0, le=1)
    window: str = "P30D"
    community: CommunityPolicy = Field(default_factory=CommunityPolicy)

    @classmethod
    def load(cls, path: Path) -> Policy:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
