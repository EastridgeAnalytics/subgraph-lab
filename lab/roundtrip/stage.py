"""The way out, step one: results into the run's push store (page 01, "Stage"; decisions 22 and 23).

`stage(run, df, key, kind)` takes one table with a key column and writes it into `run.paths.push_store` through
`run.push_store()`. Nothing here touches the run store, the master graph or `relate`. The push store holds only
keys, `risk_*` properties and the lab's own `Alert`, `RiskCommunity` and `LabRun` nodes with their links, and has a
uniqueness constraint per label, so `relate push` can merge on keys.

Three kinds, all MERGEd by key, so staging the same table twice changes nothing:

| kind | key column | other columns | writes |
|---|---|---|---|
| `account` | the mapping's account key | `risk_*`, checked by `staged_errors` | the account, with its `risk_*`, `risk_run_id`, `risk_at` |
| `alert` | `alert_key` | `account_key` (required), the rest of `ALERT_PROPS` | `(:Alert)-[:ALERT_ON]->(account)` |
| `community` | `community_key` | `member_keys` (required, a list), the rest of `COMMUNITY_PROPS` | `(account)-[:IN_RISK_COMMUNITY]->(:RiskCommunity)`, `risk_community_key` on each member |

Maps (evidence, score parts, factor lists) are stored as JSON strings and lists of floats stay lists of floats.
Timestamps are UTC ISO-8601 strings only; any date or datetime value is refused, because `relate push` 1.0.1 fails
with HTTP 400 on a zoned datetime (decision 23). A null value is left out, not written: in Cypher `SET n += {p: null}`
removes the property.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, get_args

import polars as pl

from lab.common.ids import canonical_json, iso_utc
from lab.common.log import get_logger
from lab.common.store import rows
from lab.contracts.graph import (
    ALERT_KEY,
    ALERT_LABEL,
    ALERT_ON,
    ALERT_PROPS,
    COMMUNITY_KEY,
    COMMUNITY_LABEL,
    COMMUNITY_PROPS,
    IN_COMMUNITY,
    LABRUN_KEY,
    LABRUN_LABEL,
    Disposition,
    GraphMapping,
)
from lab.contracts.manifest import StepStatus
from lab.contracts.staged import StagedKind, staged_errors
from lab.roundtrip import ledger
from lab.roundtrip.indexes import unique_constraint
from lab.roundtrip.loader import load_rows
from lab.roundtrip.queries import ident
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("roundtrip.stage")

MEMBER_KEYS = "member_keys"
DISPOSITIONS = get_args(Disposition)
_FLOATS = (pl.Float32, pl.Float64)
_INTS = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)
_TEXT = (pl.String, pl.Utf8)


class StagedError(ValueError):
    """A table that breaks the staged-table contract. `errors` lists every way it does."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("staged table rejected: " + "; ".join(errors))


class NothingStaged(RuntimeError):
    """There is nothing in the push store to review or push."""


# ---- what the push store holds ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class NodeShape:
    label: str
    key: str


@dataclass(frozen=True)
class RelShape:
    type: str
    start: NodeShape
    end: NodeShape

    @property
    def entity(self) -> str:
        """The name `relate push` gives this relationship in its report: `(Alert)-[:ALERT_ON]->(Account)`."""
        return f"({self.start.label})-[:{self.type}]->({self.end.label})"


def account_shape(mapping: GraphMapping) -> NodeShape:
    return NodeShape(mapping.account_label, mapping.account_key)


ALERT_SHAPE = NodeShape(ALERT_LABEL, ALERT_KEY)
COMMUNITY_SHAPE = NodeShape(COMMUNITY_LABEL, COMMUNITY_KEY)
LABRUN_SHAPE = NodeShape(LABRUN_LABEL, LABRUN_KEY)


def node_shapes(mapping: GraphMapping) -> list[NodeShape]:
    """The labels a push sends, in the order it sends them. `LabRun` is its own push and is not here."""
    return [account_shape(mapping), ALERT_SHAPE, COMMUNITY_SHAPE]


def rel_shapes(mapping: GraphMapping) -> list[RelShape]:
    account = account_shape(mapping)
    return [RelShape(ALERT_ON, ALERT_SHAPE, account), RelShape(IN_COMMUNITY, account, COMMUNITY_SHAPE)]


def ensure_push_constraints(conn, mapping: GraphMapping) -> None:
    """A uniqueness constraint per label in the push store (decision 22). Idempotent."""
    for shape in (*node_shapes(mapping), LABRUN_SHAPE):
        conn.execute(unique_constraint(shape.label, shape.key))


def push_store_counts(conn, mapping: GraphMapping) -> dict[str, int]:
    """What is staged, counted in the push store. All zeros means there is nothing to review or push."""
    a = f"(a:{ident(mapping.account_label)})"
    count = lambda q: rows(conn, q)[0]["n"]  # noqa: E731
    accounts = count(f"MATCH {a} RETURN count(a) AS n")
    alerts = count(f"MATCH (n:{ident(ALERT_LABEL)}) RETURN count(n) AS n")
    communities = count(f"MATCH (n:{ident(COMMUNITY_LABEL)}) RETURN count(n) AS n")
    vectors = count(f"MATCH {a} WHERE a.risk_embedding IS NOT NULL RETURN count(a) AS n") + count(
        f"MATCH (n:{ident(COMMUNITY_LABEL)}) WHERE n.embedding IS NOT NULL RETURN count(n) AS n"
    )
    dispositions = sum(
        count(f"MATCH (n:{ident(label)}) WHERE n.risk_disposition IS NOT NULL RETURN count(n) AS n")
        for label in (mapping.account_label, ALERT_LABEL, COMMUNITY_LABEL)
    )
    return {
        "accounts": accounts,
        "accounts_scored": count(f"MATCH {a} WHERE a.risk_score IS NOT NULL RETURN count(a) AS n"),
        "alerts": alerts,
        "communities": communities,
        "vectors": vectors,
        "dispositions": dispositions,
        "relationships": count("MATCH ()-[r]->() RETURN count(r) AS n"),
    }


def has_staged(counts: dict[str, int]) -> bool:
    return bool(counts["accounts"] + counts["alerts"] + counts["communities"])


# ---- validation ------------------------------------------------------------------------------------------------
def _has_temporal(dtype: pl.DataType) -> bool:
    if dtype.is_temporal():
        return True
    if isinstance(dtype, pl.List | pl.Array):
        return _has_temporal(dtype.inner)
    if isinstance(dtype, pl.Struct):
        return any(_has_temporal(f.dtype) for f in dtype.fields)
    return False


def _find_temporal(value: Any) -> str | None:
    """The type name of a date, datetime, time or duration anywhere inside a Python value, else None."""
    import datetime as dt

    if isinstance(value, dt.datetime | dt.date | dt.time | dt.timedelta):
        return type(value).__name__
    if isinstance(value, dict):
        return next((t for v in value.values() if (t := _find_temporal(v))), None)
    if isinstance(value, list | tuple):
        return next((t for v in value if (t := _find_temporal(v))), None)
    return None


def _is_float_list(dtype: pl.DataType) -> bool:
    return isinstance(dtype, pl.List) and dtype.inner in _FLOATS


def _bad_floats(series: pl.Series) -> str | None:
    """Why a float column cannot be sent (NaN, infinity, a null inside a list), else None."""
    if series.dtype in _FLOATS:
        if series.is_nan().any() or series.is_infinite().any():
            return "has NaN or infinite values, which the Query API cannot carry"
    elif _is_float_list(series.dtype):
        inner = series.list.eval(pl.element().is_nan() | pl.element().is_infinite() | pl.element().is_null())
        if inner.list.any().any():
            return "has a list with a NaN, infinite or null element"
    return None


def _temporal_error(name: str, what: str) -> str:
    return (
        f"column {name!r} holds {what} values; timestamps must be UTC ISO-8601 strings, for example "
        "'2026-11-12T21:45:00Z' (decision 23: relate push fails with HTTP 400 on zoned datetimes)"
    )


def _to_json_column(series: pl.Series, errors: list[str]) -> pl.Series:
    """A column of maps, or of lists that are not float lists, becomes JSON strings. Dates inside are refused."""
    values = series.to_list()
    out: list[str | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        what = _find_temporal(v)
        if what:
            errors.append(_temporal_error(series.name, what))
            return series
        out.append(canonical_json(v))
    return pl.Series(series.name, out, dtype=pl.String)


def _check_columns(df: pl.DataFrame, key: str, special: set[str], errors: list[str]) -> pl.DataFrame:
    """Dtype checks shared by alerts and communities. Returns the frame with maps turned into JSON strings."""
    for name in df.columns:
        if name == key or name in special:
            continue
        dtype = df.schema[name]
        if _has_temporal(dtype):
            errors.append(_temporal_error(name, str(dtype)))
        elif dtype == pl.Object:
            df = df.with_columns(_to_json_column(df[name], errors))
        elif isinstance(dtype, pl.Struct) or (isinstance(dtype, pl.List) and not _is_float_list(dtype)):
            df = df.with_columns(_to_json_column(df[name], errors))
        elif dtype in _FLOATS or _is_float_list(dtype):
            if why := _bad_floats(df[name]):
                errors.append(f"column {name!r} {why}")
        elif dtype in _INTS or dtype in _TEXT or dtype == pl.Boolean or dtype == pl.Null:
            continue
        else:
            errors.append(
                f"column {name!r} has type {dtype}; use a number, string, boolean, list of floats, or a map or list "
                "(stored as JSON)"
            )
    return df


def _fill(df: pl.DataFrame, name: str, value: str, errors: list[str], must_equal: bool) -> pl.DataFrame:
    """Add `name` with `value` when the frame does not give it; fill its nulls when it does."""
    if name not in df.columns:
        return df.with_columns(pl.lit(value, dtype=pl.String).alias(name))
    if df.schema[name] not in _TEXT and df.schema[name] != pl.Null:
        errors.append(f"column {name!r} must be a string, not {df.schema[name]}")
        return df
    df = df.with_columns(pl.col(name).cast(pl.String).fill_null(value))
    if must_equal and (df[name] != value).any():
        errors.append(f"column {name!r} must be this run's id {value!r} or absent")
    return df


def _key_errors(df: pl.DataFrame, key: str) -> list[str]:
    if key not in df.columns:
        return [f"no key column {key!r}; columns are {df.columns}"]
    errors = []
    if df[key].null_count():
        errors.append(f"key column {key!r} has {df[key].null_count()} nulls")
    if df[key].n_unique() != df.height:
        errors.append(f"key column {key!r} has {df.height - df[key].n_unique()} duplicate keys")
    if df.schema[key] not in (*_TEXT, *_INTS):
        errors.append(f"key column {key!r} has type {df.schema[key]}; keys are strings or integers")
    return errors


def _temporal_columns(df: pl.DataFrame) -> list[tuple[str, str]]:
    """Columns that hold a date or datetime: by dtype, or inside a Python-object column."""
    found = []
    for name, dtype in df.schema.items():
        if _has_temporal(dtype):
            found.append((name, str(dtype)))
        elif dtype == pl.Object and (what := _find_temporal(df[name].to_list())):
            found.append((name, what))
    return found


def _prepare_account(run: Run, df: pl.DataFrame, key: str) -> pl.DataFrame:
    temporal = _temporal_columns(df)
    errors = [e for e in staged_errors(df, key) if not any(f"column {n!r}" in e for n, _ in temporal)]
    errors += [_temporal_error(name, what) for name, what in temporal]
    if not errors:
        for name in df.columns:
            if name != key and (why := _bad_floats(df[name])):
                errors.append(f"column {name!r} {why}")
        df = _fill(df, "risk_run_id", run.id, errors, must_equal=True)
        df = _fill(df, "risk_at", iso_utc(), errors, must_equal=False)
    if errors:
        raise StagedError(errors)
    return df


def _prepare_lab_node(run: Run, df: pl.DataFrame, key: str, allowed: tuple[str, ...], required: tuple[str, ...],
                      special: set[str], kind: str) -> pl.DataFrame:
    errors = _key_errors(df, key)
    for name in required:
        if name not in df.columns:
            errors.append(f"a {kind} table needs a {name!r} column")
        elif df[name].null_count():
            errors.append(f"column {name!r} has {df[name].null_count()} nulls")
    for name in df.columns:
        if name != key and name not in allowed and name not in special:
            errors.append(f"column {name!r} is not an {kind} property; allowed: {sorted({*allowed, *special} - {key})}")
    if not errors:
        df = _check_columns(df, key, special, errors)
    if not errors:
        df = _fill(df, "run_id", run.id, errors, must_equal=True)
        df = _fill(df, "created_at", iso_utc(), errors, must_equal=False)
    if errors:
        raise StagedError(errors)
    return df


def _members(df: pl.DataFrame, key: str) -> pl.DataFrame:
    """The community table's member lists, checked. Raises StagedError."""
    errors: list[str] = []
    dtype = df.schema[MEMBER_KEYS]
    if not isinstance(dtype, pl.List) or not (dtype.inner in _TEXT or dtype.inner in _INTS):
        raise StagedError([f"column {MEMBER_KEYS!r} must be a list of account keys, not {dtype}"])
    sizes = df[MEMBER_KEYS].list.len()
    empty = df.filter(sizes == 0)[key].to_list()
    if empty:
        errors.append(f"communities with no members: {empty[:5]}")
    if df[MEMBER_KEYS].list.eval(pl.element().is_null()).list.any().any():
        errors.append(f"column {MEMBER_KEYS!r} has null keys")
    if errors:
        raise StagedError(errors)
    return df


# ---- writing ---------------------------------------------------------------------------------------------------
def _load(conn, statement: str, frame: pl.DataFrame, make_rows, settings) -> int:
    """Run `statement` over the frame in slices, through the loader, which halves a batch the store refuses."""
    size = max(1, settings.load_start_rows)
    total = 0
    for part in frame.iter_slices(n_rows=size):
        data = make_rows(part)
        if not data:
            continue
        stats = load_rows(conn, statement, data, start=size)
        size, total = stats.size, total + stats.rows
    return total


def _prop_rows(part: pl.DataFrame, key: str, drop: set[str] = frozenset()) -> list[dict]:
    """`{"key", "props"}` rows. A null is left out: `SET n += {p: null}` would remove the property."""
    out = []
    for rec in part.to_dicts():
        props = {k: v for k, v in rec.items() if k != key and k not in drop and v is not None}
        row = {"key": rec[key], "props": props}
        if "account_key" in rec:  # an alert's account, for the ALERT_ON link
            row["account"] = rec["account_key"]
        out.append(row)
    return out


def _next_parquet(run: Run, kind: str):
    run.paths.staged.mkdir(parents=True, exist_ok=True)
    n = len(list(run.paths.staged.glob(f"{kind}-*.parquet"))) + 1
    return run.paths.staged / f"{kind}-{n}.parquet"


def _record(run: Run, counts: dict[str, int]) -> None:
    """Add to the manifest's `stage` counts, and drop a half-finished push's record: what landed was a different push."""
    step = run.manifest.step("stage")
    if step.status is not StepStatus.DONE:
        run.manifest.start("stage")
    total = {k: step.counts.get(k, 0) + n for k, n in counts.items()}
    run.manifest.finish("stage", total)
    if ledger.invalidate(run.manifest):
        log.info("staged after a push stopped; the next push starts over instead of resuming")
    run.save()


def stage(run: Run, df: pl.DataFrame, key: str, kind: str = "account") -> int:
    """Write one results table into the push store. Returns the rows staged.

    Raises `StagedError` (a ValueError with `.errors`) when the table breaks the contract; nothing is written then.
    For `alert` and `community` the key column is fixed (`alert_key`, `community_key`); `key` may be that name or the
    account key the facade passes by default.
    """
    kind = StagedKind(kind)
    mapping = run.recipe.graph
    settings = get_settings()
    shape = {StagedKind.ACCOUNT: account_shape(mapping), StagedKind.ALERT: ALERT_SHAPE,
             StagedKind.COMMUNITY: COMMUNITY_SHAPE}[kind]
    if kind is not StagedKind.ACCOUNT and key not in (shape.key, mapping.account_key):
        raise StagedError([f"{kind.value} tables are keyed by {shape.key!r}, not {key!r}"])
    key = shape.key if kind is not StagedKind.ACCOUNT else key

    counts: dict[str, int]
    if kind is StagedKind.ACCOUNT:
        df = _prepare_account(run, df, key)
        statement = (
            f"UNWIND $rows AS r MERGE (n:{ident(shape.label)} {{{ident(shape.key)}: r.key}}) SET n += r.props"
        )
        counts = {"accounts": df.height}
    elif kind is StagedKind.ALERT:
        df = _prepare_lab_node(run, df, key, ALERT_PROPS, ("account_key",), set(), "alert")
        account = account_shape(mapping)
        statement = (
            f"UNWIND $rows AS r MERGE (n:{ident(shape.label)} {{{ident(shape.key)}: r.key}}) SET n += r.props "
            f"WITH n, r MERGE (a:{ident(account.label)} {{{ident(account.key)}: r.account}}) "
            f"MERGE (n)-[:{ident(ALERT_ON)}]->(a)"
        )
        counts = {"alerts": df.height}
    else:
        if MEMBER_KEYS not in df.columns:
            raise StagedError([f"a community table needs a {MEMBER_KEYS!r} column"])
        df = _prepare_lab_node(run, df, key, COMMUNITY_PROPS, (MEMBER_KEYS,), {MEMBER_KEYS}, "community")
        df = _members(df, key)
        statement = (
            f"UNWIND $rows AS r MERGE (n:{ident(shape.label)} {{{ident(shape.key)}: r.key}}) SET n += r.props"
        )
        counts = {"communities": df.height}

    if df.height == 0:
        return 0
    conn = run.push_store()
    ensure_push_constraints(conn, mapping)
    drop = {MEMBER_KEYS} if kind is StagedKind.COMMUNITY else set()
    try:
        _load(conn, statement, df, lambda part: _prop_rows(part, key, drop), settings)
        if kind is StagedKind.COMMUNITY:
            account = account_shape(mapping)
            link = (
                f"UNWIND $rows AS r MERGE (a:{ident(account.label)} {{{ident(account.key)}: r.member}}) "
                f"MERGE (c:{ident(COMMUNITY_LABEL)} {{{ident(COMMUNITY_KEY)}: r.community}}) "
                f"MERGE (a)-[:{ident(IN_COMMUNITY)}]->(c) SET a.risk_community_key = r.community"
            )
            members = df.select(pl.col(key).alias("community"), pl.col(MEMBER_KEYS)).explode(MEMBER_KEYS).rename(
                {MEMBER_KEYS: "member"}
            )
            _load(conn, link, members, lambda part: part.to_dicts(), settings)
            counts["members"] = members.height
    except Exception as e:
        run.manifest.fail("stage", f"{type(e).__name__}: {e}"[:500])
        run.save()
        raise
    df.write_parquet(_next_parquet(run, kind.value))
    _record(run, counts)
    log.info("staged %d %s rows in the push store of %s", df.height, kind.value, run.id)
    return df.height


def stage_disposition(run: Run, kind: str, key: Any, disposition: str, by: str) -> None:
    """The reviewer's decision, staged to travel with the next push (page 04).

    Sets `risk_disposition`, `risk_disposition_by` and `risk_disposition_at` on the account, alert or community
    in the push store. An account is MERGEd by key when it is not there yet, and carries this run's `risk_run_id`
    either way, so `retract` finds an account whose only result is a disposition; an alert or community must already
    be staged (they have their own `run_id`). The decision reaches the master graph only with a push.
    """
    kind = StagedKind(kind)
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {list(DISPOSITIONS)}, not {disposition!r}")
    if not by or not str(by).strip():
        raise ValueError("a disposition records who made it: `by` is empty")
    mapping = run.recipe.graph
    shape = {StagedKind.ACCOUNT: account_shape(mapping), StagedKind.ALERT: ALERT_SHAPE,
             StagedKind.COMMUNITY: COMMUNITY_SHAPE}[kind]
    conn = run.push_store()
    ensure_push_constraints(conn, mapping)
    params = {"key": key, "d": disposition, "by": str(by), "at": iso_utc(), "run": run.id}
    sets = "n.risk_disposition = $d, n.risk_disposition_by = $by, n.risk_disposition_at = $at"
    label, prop = ident(shape.label), ident(shape.key)
    if kind is StagedKind.ACCOUNT:
        conn.execute(f"MERGE (n:{label} {{{prop}: $key}}) SET {sets}, n.risk_run_id = $run", params)
    else:
        hit = rows(conn, f"MATCH (n:{label} {{{prop}: $key}}) SET {sets} RETURN count(n) AS c", params)[0]["c"]
        if not hit:
            raise ValueError(f"no staged {kind.value} {key!r} in the push store of {run.id}; stage it first")
    _record(run, {"dispositions": 1})
