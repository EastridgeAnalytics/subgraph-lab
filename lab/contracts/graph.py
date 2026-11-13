"""Node shapes and keys: what the lab reads from the master graph and what it writes back.

The lab never assumes its own generator made the data. A recipe names the customer's labels, keys and types in a
`GraphMapping`; the defaults below are the generator's names.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IdentifierKind = Literal["device", "email", "address", "phone", "other"]


class IdentifierLink(BaseModel):
    """A shared identifier: an account links to it, and accounts that share one are linked for communities."""

    rel_type: str
    label: str
    key: str
    kind: IdentifierKind


class GraphMapping(BaseModel):
    account_label: str = "Account"
    account_key: str = "account_id"
    account_opened: str | None = "opened"
    payment_type: str = "TX"
    payment_key: str = "tx_id"
    payment_amount: str = "amount"
    payment_time: str = "ts"
    identifiers: list[IdentifierLink] = Field(
        default_factory=lambda: [
            IdentifierLink(rel_type="USES_DEVICE", label="Device", key="device_id", kind="device"),
            IdentifierLink(rel_type="HAS_EMAIL", label="Email", key="email_id", kind="email"),
            IdentifierLink(rel_type="HAS_ADDRESS", label="Address", key="address_id", kind="address"),
        ]
    )


# What the lab writes. Labels, keys and relationship types are fixed; the account label and key come from the
# mapping. Every written property on an account starts with risk_.
ALERT_LABEL, ALERT_KEY = "Alert", "alert_key"
COMMUNITY_LABEL, COMMUNITY_KEY = "RiskCommunity", "community_key"
LABRUN_LABEL, LABRUN_KEY = "LabRun", "run_id"
ALERT_ON = "ALERT_ON"  # (:Alert)-[:ALERT_ON]->(:Account)
IN_COMMUNITY = "IN_RISK_COMMUNITY"  # (:Account)-[:IN_RISK_COMMUNITY]->(:RiskCommunity)

RISK_PREFIX = "risk_"

# The run store's search surface (decision 29). The first pass writes the embedding and the summary; the indexes
# are created after every pull. Community nodes in the run store carry their own embedding and summary.
EMBEDDING_PROP = "risk_embedding"  # pushed back too
SUMMARY_PROP = "lab_summary"  # the template account summary; stays in the run store
ACCOUNT_VECTOR_INDEX = "account_embedding"
ACCOUNT_TEXT_INDEX = "account_summary"
COMMUNITY_VECTOR_INDEX = "community_embedding"
COMMUNITY_TEXT_INDEX = "community_summary"

ACCOUNT_RESULT_PROPS = (
    "risk_score",
    "risk_score_model",
    "risk_score_rules",
    "risk_factors",  # JSON string: [{"id", "score", "weight"}]
    "risk_run_id",
    "risk_at",  # UTC ISO-8601 string (decision 23)
    "risk_embedding",  # list of floats
    "risk_community_key",
    "risk_disposition",
    "risk_disposition_by",
    "risk_disposition_at",
)

ALERT_PROPS = (
    "alert_key", "run_id", "account_key", "window", "score", "created_at",
    "summary", "summary_source", "summary_model", "evidence_hash", "evidence",
    "risk_disposition", "risk_disposition_by", "risk_disposition_at",
)

COMMUNITY_PROPS = (
    "community_key", "run_id", "score", "size", "score_parts", "summary", "summary_source", "summary_model",
    "evidence_hash", "evidence", "embedding", "created_at",
    "risk_disposition", "risk_disposition_by", "risk_disposition_at",
)

LABRUN_PROPS = (
    "run_id", "started_at", "finished_at", "recipe_hash", "model_hash", "factor_version",
    "route_pull", "route_push", "counts",
)

Disposition = Literal["confirmed", "false_positive", "needs_more"]
