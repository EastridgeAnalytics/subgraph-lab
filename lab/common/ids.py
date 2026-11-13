"""Run ids, keys and hashes. Every key the lab writes comes from here."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any


def run_id(now: datetime | None = None, suffix: str | None = None) -> str:
    """run-20261112T214500Z-1a2b3c. Sortable by time; the suffix keeps two runs in one second apart."""
    now = now or datetime.now(UTC)
    return f"run-{now.strftime('%Y%m%dT%H%M%SZ')}-{suffix or secrets.token_hex(3)}"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def alert_key(run: str, account_key: str, window: str) -> str:
    """sha1(run_id | account_id | window), page 01."""
    return _sha1(f"{run}|{account_key}|{window}")


def community_key(run: str, member_keys: list[str] | set[str]) -> str:
    """sha1(run_id | lowest member key), page 01. The same members in the same run always give the same key."""
    if not member_keys:
        raise ValueError("a community needs at least one member")
    return _sha1(f"{run}|{min(member_keys)}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def evidence_hash(obj: Any) -> str:
    """SHA-256 of the canonical JSON. Two evidence objects with the same content have the same hash."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def content_hash(obj: Any, length: int = 16) -> str:
    """A short SHA-256 for recipes, factor files and model weights."""
    data = obj if isinstance(obj, bytes) else canonical_json(obj).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:length]


def iso_utc(dt: datetime | None = None) -> str:
    """UTC ISO-8601 with a Z, the only timestamp form the push store carries (decision 23)."""
    dt = dt or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
