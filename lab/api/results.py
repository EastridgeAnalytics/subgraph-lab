"""Reading a run: results, evidence, the timeline, the models list, show-code, the diff between two runs.

Everything here reads. Results come from the run's staged Parquet (the scores and the alert and community tables, as
staged) and, for the reviewer's decisions, from the push store; the timeline comes from the run store. Stores are
opened read-only for the length of one call, so a request never meets the worker's writer, and nothing here writes.

A table that is not there yet is not an error: a run before `score` has no accounts, and a run before track 3a has no
communities, and both come back as an empty page.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import numpy as np
import polars as pl
from pydantic import ValidationError

from lab.api.errors import ApiError, NotFound, NotReady
from lab.api.view import RunView, read_text
from lab.common.ids import iso_utc
from lab.common.store import RunPaths, open_store, rows
from lab.contracts.api import (
    AccountRow,
    AccountsPage,
    CommunitiesPage,
    CommunityDetail,
    CommunityRow,
    GraphEdge,
    GraphNode,
    ModelInfo,
    ModelsList,
    ShowCode,
    Timeline,
    TimelinePoint,
)
from lab.contracts.evidence import Evidence, Summary
from lab.contracts.factors import Factor, FactorFile, Policy
from lab.contracts.graph import ALERT_KEY, ALERT_LABEL, COMMUNITY_KEY, COMMUNITY_LABEL
from lab.contracts.recipe import ModelName
from lab.contracts.tools import ExplainOut, GraphDiffOut
from lab.roundtrip.delta import BUCKETS, bucket
from lab.roundtrip.queries import ident
from lab.settings import get_settings

DEFAULT_LIMIT = 100  # rows in a page when the request does not say
MAX_LIMIT = 1000
GRAPH_NODES = 500  # members drawn for one community; `evidence.member_count` says how many there are
SIMILAR = 5  # "communities like this one"

SOURCES = ("api", "agent", "template")


# ---- the staged tables ----------------------------------------------------------------------------------------
def _number(path) -> int:
    m = re.search(r"-(\d+)\.parquet$", path.name)
    return int(m.group(1)) if m else 0


def staged_frame(paths: RunPaths, kind: str, key: str) -> pl.DataFrame | None:
    """Every `staged/<kind>-N.parquet` in the order staged, one row per key (the latest staging wins). None when the
    run has staged none of that kind."""
    files = sorted(paths.staged.glob(f"{kind}-*.parquet"), key=_number) if paths.staged.exists() else []
    if not files:
        return None
    try:
        df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    except Exception as e:  # noqa: BLE001 (a file the worker is still writing)
        raise NotReady(f"the staged {kind} table of {paths.run_id} could not be read just now ({type(e).__name__}); "
                       "try again in a moment") from e
    if key not in df.columns:
        raise NotReady(f"the staged {kind} table of {paths.run_id} has no {key!r} column")
    return df.unique(subset=[key], keep="last", maintain_order=True)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def dispositions(run: RunView) -> dict[str, dict[str, str]]:
    """The reviewer's decisions staged in the push store: kind -> key -> disposition. Empty when nothing is staged."""
    out: dict[str, dict[str, str]] = {"account": {}, "alert": {}, "community": {}}
    if not run.paths.push_store.exists():
        return out
    m = run.recipe.graph
    try:
        conn = open_store(run.paths.push_store, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise NotReady(f"the push store of {run.id} could not be read just now ({type(e).__name__}); "
                       "try again in a moment") from e
    try:
        for kind, label, key in (("account", m.account_label, m.account_key), ("alert", ALERT_LABEL, ALERT_KEY),
                                 ("community", COMMUNITY_LABEL, COMMUNITY_KEY)):
            found = rows(conn, f"MATCH (n:{ident(label)}) WHERE n.risk_disposition IS NOT NULL "
                               f"RETURN n.{ident(key)} AS k, n.risk_disposition AS d")
            out[kind] = {str(r["k"]): r["d"] for r in found}
    except Exception as e:  # noqa: BLE001
        raise NotReady(f"the push store of {run.id} could not be read just now ({type(e).__name__}); "
                       "try again in a moment") from e
    finally:
        conn.close()
    return out


def _page(df: pl.DataFrame, limit: int, offset: int) -> pl.DataFrame:
    return df.slice(max(0, offset), max(0, min(limit, MAX_LIMIT)))


def _factors(text: Any) -> list[dict]:
    try:
        value = json.loads(text) if isinstance(text, str) else []
    except ValueError:
        return []
    return value if isinstance(value, list) else []


# ---- accounts and communities --------------------------------------------------------------------------------
def accounts_page(run: RunView, limit: int = DEFAULT_LIMIT, offset: int = 0) -> AccountsPage:
    """The staged account scores, highest first. An account's disposition is its own, else its alert's."""
    key = run.recipe.graph.account_key
    df = staged_frame(run.paths, "account", key)
    if df is None:
        return AccountsPage(total=0, rows=[])
    alerts = staged_frame(run.paths, "alert", ALERT_KEY)
    alert_of = dict(zip(alerts["account_key"].cast(pl.Utf8), alerts[ALERT_KEY], strict=True)) if alerts is not None else {}
    decided = dispositions(run)
    df = df.sort(["risk_score", key], descending=[True, False], nulls_last=True)
    out = []
    for r in _page(df, limit, offset).iter_rows(named=True):
        k = str(r[key])
        alert = alert_of.get(k)
        out.append(AccountRow(
            key=k, risk_score=r.get("risk_score") or 0.0, risk_score_model=r.get("risk_score_model"),
            risk_score_rules=r.get("risk_score_rules"), factors=_factors(r.get("risk_factors")),
            community_key=_text(r.get("risk_community_key")), alert_key=alert,
            disposition=decided["account"].get(k) or (decided["alert"].get(alert) if alert else None),
        ))
    return AccountsPage(total=df.height, rows=out)


def _community_row(r: dict, decided: dict[str, str]) -> CommunityRow:
    members = r.get("member_keys")
    size = r.get("size")
    return CommunityRow(
        key=str(r[COMMUNITY_KEY]), size=int(size if size is not None else len(members or [])),
        score=float(r.get("score") or 0.0), summary=_text(r.get("summary")), summary_source=_text(r.get("summary_source")),
        disposition=decided.get(str(r[COMMUNITY_KEY])),
    )


def communities_page(run: RunView, limit: int = DEFAULT_LIMIT, offset: int = 0) -> CommunitiesPage:
    """The staged communities, highest score first. Empty until track 3a stages any."""
    df = staged_frame(run.paths, "community", COMMUNITY_KEY)
    if df is None:
        return CommunitiesPage(total=0, rows=[])
    decided = dispositions(run)["community"]
    df = df.sort(["score", COMMUNITY_KEY], descending=[True, False], nulls_last=True)
    return CommunitiesPage(total=df.height, rows=[_community_row(r, decided) for r in _page(df, limit, offset).iter_rows(named=True)])


# ---- evidence and summaries ------------------------------------------------------------------------------------
def evidence_of(row: dict) -> Evidence | None:
    """The row's `evidence` column as the checked `Evidence` object, or None when the row has none that passes (the
    scoring step's own alert evidence is a different, older shape; the summaries track replaces it)."""
    text = row.get("evidence")
    if not isinstance(text, str):
        return None
    try:
        return Evidence.model_validate_json(text)
    except (ValidationError, ValueError):
        return None


def summary_of(row: dict, key: str, evidence: Evidence) -> Summary | None:
    text = row.get("summary")
    source = row.get("summary_source")
    if not isinstance(text, str) or source not in SOURCES:
        return None
    return Summary(key=key, text=text, source=source, model=_text(row.get("summary_model")),
                   evidence_hash=_text(row.get("evidence_hash")) or evidence.hash())


def _find_subject(run: RunView, key: str) -> tuple[str, dict]:
    """The staged alert or community a key names: an alert key, a community key, or an account key that has an alert."""
    communities = staged_frame(run.paths, "community", COMMUNITY_KEY)
    if communities is not None:
        hit = communities.filter(pl.col(COMMUNITY_KEY) == key)
        if hit.height:
            return "community", hit.row(0, named=True)
    alerts = staged_frame(run.paths, "alert", ALERT_KEY)
    if alerts is not None:
        hit = alerts.filter((pl.col(ALERT_KEY) == key) | (pl.col("account_key").cast(pl.Utf8) == key))
        if hit.height:
            return "alert", hit.sort("score", descending=True).row(0, named=True)
    raise NotFound(f"run {run.id} has no alert, community or alerted account {key!r}")


def explain(run: RunView, key: str) -> ExplainOut:
    """The evidence object for an alert, a community, or an account's alert. A clear not-yet error until the summaries
    track writes evidence objects into the staged rows."""
    kind, row = _find_subject(run, key)
    evidence = evidence_of(row)
    if evidence is None:
        raise NotReady(
            f"no evidence object for this {kind} yet: they are written by the summaries step (track 3b), which has "
            "not staged one for this run. The alert's scoring evidence is on the Alert node in the push store, "
            "readable through Relate's read_cypher."
        )
    return ExplainOut(evidence=evidence, summary=summary_of(row, str(row[ALERT_KEY if kind == "alert" else COMMUNITY_KEY]), evidence))


# ---- one community ---------------------------------------------------------------------------------------------
def _similar(df: pl.DataFrame, key: str, decided: dict[str, str]) -> list[CommunityRow]:
    """The communities whose embedding is nearest this one's, by cosine. Empty without embeddings."""
    if "embedding" not in df.columns:
        return []
    mine = df.filter(pl.col(COMMUNITY_KEY) == key)["embedding"][0]
    others = df.filter((pl.col(COMMUNITY_KEY) != key) & pl.col("embedding").is_not_null())
    if mine is None or not others.height:
        return []
    m = np.asarray(mine, dtype=float)
    matrix = np.asarray(others["embedding"].to_list(), dtype=float)
    norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(m) or 1.0)
    sim = np.divide(matrix @ m, norms, out=np.zeros(len(matrix)), where=norms > 0)
    top = np.argsort(-sim, kind="stable")[:SIMILAR]
    return [_community_row(others.row(int(i), named=True), decided) for i in top]


def _community_graph(run: RunView, member_keys: list[str], scores: dict[str, float]) -> tuple[list[GraphNode], list[GraphEdge]]:
    """The members as nodes, the payments between them as one edge per pair (count and amount), and the identifiers
    two or more of them share. At most `GRAPH_NODES` members. Account ids are their keys; an identifier's id is
    `<Label>:<key>`."""
    m = run.recipe.graph
    keys = member_keys[:GRAPH_NODES]
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    conn = open_store(run.paths.store, read_only=True)
    try:
        acct, key = ident(m.account_label), ident(m.account_key)
        for r in rows(conn, f"MATCH (a:{acct}) WHERE a.{key} IN $keys RETURN a.{key} AS k, a.lab_summary AS s", {"keys": keys}):
            props = {"risk_score": scores[str(r["k"])]} if str(r["k"]) in scores else {}
            if r["s"]:
                props["summary"] = r["s"]
            nodes.append(GraphNode(id=str(r["k"]), label=m.account_label, props=props))
        pay, amount = ident(m.payment_type), ident(m.payment_amount)
        for r in rows(conn, f"MATCH (s:{acct})-[t:{pay}]->(d:{acct}) WHERE s.{key} IN $keys AND d.{key} IN $keys "
                            f"RETURN s.{key} AS s, d.{key} AS d, count(t) AS n, sum(t.{amount}) AS total", {"keys": keys}):
            edges.append(GraphEdge(source=str(r["s"]), target=str(r["d"]), type=m.payment_type,
                                   props={"payments": r["n"], "amount": r["total"] or 0.0}))
        for link in m.identifiers:
            found = rows(conn, f"MATCH (a:{acct})-[:{ident(link.rel_type)}]->(x:{ident(link.label)}) WHERE a.{key} IN $keys "
                               f"WITH x, collect(a.{key}) AS members WHERE size(members) > 1 "
                               f"RETURN x.{ident(link.key)} AS x, members", {"keys": keys})
            for r in found:
                node_id = f"{link.label}:{r['x']}"
                nodes.append(GraphNode(id=node_id, label=link.label, props={"key": str(r["x"]), "kind": link.kind}))
                edges += [GraphEdge(source=str(a), target=node_id, type=link.rel_type) for a in r["members"]]
    finally:
        conn.close()
    return nodes, edges


def community_detail(run: RunView, key: str) -> CommunityDetail:
    """One community: its row, score parts, evidence, summary, members on a graph and the communities like it.
    404 when the run has no such community (before track 3a, none); 409 when it has no evidence object yet."""
    df = staged_frame(run.paths, "community", COMMUNITY_KEY)
    hit = df.filter(pl.col(COMMUNITY_KEY) == key) if df is not None else None
    if hit is None or not hit.height:
        raise NotFound(f"run {run.id} has no community {key!r}" + ("" if df is not None else " (no communities are staged)"))
    row = hit.row(0, named=True)
    evidence = evidence_of(row)
    if evidence is None:
        raise NotReady(f"community {key} has no evidence object yet; the summaries step (track 3b) writes it")
    decided = dispositions(run)["community"]
    members = [str(k) for k in (row.get("member_keys") or evidence.member_keys)]
    accounts = staged_frame(run.paths, "account", run.recipe.graph.account_key)
    scores = ({str(k): s for k, s in zip(accounts[run.recipe.graph.account_key], accounts["risk_score"], strict=True)}
              if accounts is not None else {})
    nodes, edges = _community_graph(run, members, scores)
    parts = row.get("score_parts")
    score_parts = json.loads(parts) if isinstance(parts, str) else dict(evidence.score_parts)
    return CommunityDetail(
        row=_community_row(row, decided), score_parts={k: float(v) for k, v in score_parts.items()}, evidence=evidence,
        summary=summary_of(row, key, evidence), nodes=nodes, edges=edges, similar=_similar(df, key, decided),
    )


# ---- the timeline ----------------------------------------------------------------------------------------------
def parse_as_of(text: str) -> datetime:
    """An ISO-8601 date or datetime as a UTC datetime. A bare date means the end of that day."""
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ApiError(422, f"as_of must be an ISO-8601 date or datetime such as 2026-07-01 or 2026-07-01T12:00:00Z, got {text!r}") from e
    if len(text.strip()) == 10:  # a date, not a datetime
        value = value + timedelta(days=1) - timedelta(seconds=1)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(text: str | None) -> str:
    return iso_utc(datetime.fromisoformat(text.replace("Z", "+00:00"))) if text else ""


def timeline(run: RunView, as_of: str | None = None) -> Timeline:
    """Payments per day in the run store, up to `as_of` (none means to the end): the day's payments and amount (to the cent), and
    the accounts that sent or received one that day. `start` and `end` are the first and last payment in the whole
    store, so a slider has its bounds whatever `as_of` is. A day is a UTC day when the payments are stored in UTC."""
    m = run.recipe.graph
    if not run.paths.store.exists():
        raise NotReady(f"run {run.id} has no store yet: its pull has not finished")
    cutoff = iso_utc(parse_as_of(as_of)) if as_of else None
    acct, key, pay, ts, amount = (ident(m.account_label), ident(m.account_key), ident(m.payment_type),
                                  ident(m.payment_time), ident(m.payment_amount))
    match = f"MATCH (s:{acct})-[t:{pay}]->(d:{acct})"
    where = f" WHERE t.{ts} <= datetime($as_of)" if cutoff else ""
    params = {"as_of": cutoff} if cutoff else {}
    try:
        conn = open_store(run.paths.store, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise NotReady(f"the store of {run.id} could not be read just now ({type(e).__name__}); try again in a moment") from e
    try:
        bounds = rows(conn, f"{match} RETURN toString(min(t.{ts})) AS lo, toString(max(t.{ts})) AS hi")[0]
        daily = rows(conn, f"{match}{where} RETURN toString(date(t.{ts})) AS day, count(t) AS payments, "
                           f"sum(t.{amount}) AS amount ORDER BY day", params)
        active = rows(conn, f"{match}{where} WITH date(t.{ts}) AS day, s, d UNWIND [s.{key}, d.{key}] AS k "
                            f"RETURN toString(day) AS day, count(DISTINCT k) AS accounts", params)
    finally:
        conn.close()
    accounts = {r["day"]: r["accounts"] for r in active}
    points = [TimelinePoint(at=f"{r['day']}T00:00:00Z", accounts=accounts.get(r["day"], 0), payments=r["payments"],
                            amount=round(float(r["amount"] or 0.0), 2)) for r in daily]
    return Timeline(start=_iso(bounds["lo"]), end=_iso(bounds["hi"]), as_of=cutoff, points=points)


# ---- the models list ------------------------------------------------------------------------------------------
MODELS: dict[str, tuple[str, str]] = {
    "node2vec": ("Embeds every node from its neighbours (PyG's Node2Vec). The embeddings feed search and similar accounts.",
                 "examples/node2vec"),
    "tgn": ("Scores each account from its timed payment events (PyG's TGN example). Lands as the tgn risk factor.",
            "examples/tgn"),
    "communities": ("Finds communities in the slice, by payments and shared identifiers: connected components, then "
                    "seeded Louvain (NetworkX), and scores them.", "examples/communities"),
    "velocity": ("Compares each account's outflow with its own forecast (statsforecast). Lands as the velocity_anomaly "
                 "risk factor.", "examples/velocity"),
}


def _factor_code_path(factor: Factor, base) -> str:
    if factor.kind == "cypher":
        return (base / (factor.query or "")).as_posix()
    if factor.kind == "python":
        return (factor.function or "").partition(":")[0].replace(".", "/") + ".py"
    return factor.source or ""


def models_list() -> ModelsList:
    """The four models and every factor in the settings' factor file, with the factor version and the policy."""
    s = get_settings()
    try:
        factor_file = FactorFile.load(s.factors_file)
        policy = Policy.load(s.policy_file)
    except (OSError, ValueError) as e:  # a missing file, a YAML error, a schema error: all say what and where
        raise ApiError(500, f"the factor or policy file could not be read: {e}") from e
    models = [ModelInfo(name=name, kind="model", description=MODELS[name][0], code_path=MODELS[name][1])
              for name in get_args(ModelName)]
    base = s.factors_file.parent
    models += [ModelInfo(name=f.id, kind="factor", description=f.description or f.name,
                         code_path=_factor_code_path(f, base), factor=f) for f in factor_file.factors]
    return ModelsList(models=models, factor_version=factor_file.version, policy=policy.model_dump(mode="json"))


# ---- show code --------------------------------------------------------------------------------------------------
_STEP_CODE = {
    "pull": ("run.pull()", "relate pull: the slice from the master graph into this run's store, then the search indexes"),
    "first_pass": ("run.first_pass()", "Node2Vec embeddings and the account summaries"),
    "slice": ("run.search()", "hybrid search, then the hops: the slice the models work on"),
    "models": ("examples.tgn.run_model(run, current_slice(run))", "the TGN scores"),
    "score": ("run.score()", "factors, the policy, communities, summaries; staged in the push store"),
    "review": ("run.preview_push()", "the dry run and the delta; nothing is sent"),
}


def show_code(run: RunView) -> ShowCode:
    """The run's recipe as saved, and the Python that runs it: the steps its recipe asks for, in order."""
    from lab.pipeline import wanted

    steps = wanted(run)
    lines = [
        '"""This run as Python. `lab.pipeline.run_recipe(recipe)` does exactly this, one step after another."""',
        "from pathlib import Path",
        "",
        "from lab.contracts.recipe import Recipe",
        "from lab.runs import Run",
    ]
    if "models" in steps:
        lines += ["from lab.slice.search import current_slice", "import examples.tgn"]
    lines += ["", 'recipe = Recipe.load(Path("recipe.yaml"))  # the YAML above', "run = Run.create(recipe)", ""]
    for step in steps:
        code, what = _STEP_CODE[step]
        lines.append(f"{code}  # {what}")
    lines += ["", "# A person reads the review, then confirms. This is the only call that writes to the master graph:",
              "# run.apply_push(confirm=True)"]
    return ShowCode(recipe_yaml=read_text(run.paths.recipe), python="\n".join(lines) + "\n")


# ---- two runs ---------------------------------------------------------------------------------------------------
def graph_diff(run_a: RunView, run_b: RunView) -> GraphDiffOut:
    """What changed from run A to run B, from their staged tables: accounts in both and how far their scores moved,
    alerted accounts that are new or gone, and each run's community count."""
    def table(run: RunView, kind: str, key: str) -> pl.DataFrame | None:
        return staged_frame(run.paths, kind, key)

    ka, kb = run_a.recipe.graph.account_key, run_b.recipe.graph.account_key
    a, b = table(run_a, "account", ka), table(run_b, "account", kb)
    moves = dict.fromkeys(BUCKETS, 0)
    both = 0
    if a is not None and b is not None:
        joined = a.select(pl.col(ka).cast(pl.Utf8).alias("key"), pl.col("risk_score").alias("a")).join(
            b.select(pl.col(kb).cast(pl.Utf8).alias("key"), pl.col("risk_score").alias("b")), on="key", how="inner")
        both = joined.height
        for before, after in zip(joined["a"], joined["b"], strict=True):
            moves[bucket(float(after or 0.0) - float(before or 0.0))] += 1

    def alerted(run: RunView) -> set[str]:
        df = table(run, "alert", ALERT_KEY)
        return set(df["account_key"].cast(pl.Utf8)) if df is not None else set()

    def count(run: RunView) -> int:
        df = table(run, "community", COMMUNITY_KEY)
        return df.height if df is not None else 0

    in_a, in_b = alerted(run_a), alerted(run_b)
    return GraphDiffOut(accounts_both=both, score_changes=moves, alerts_new=len(in_b - in_a), alerts_gone=len(in_a - in_b),
                        communities_a=count(run_a), communities_b=count(run_b))


__all__ = ["accounts_page", "communities_page", "community_detail", "dispositions", "explain", "graph_diff",
           "models_list", "show_code", "timeline"]
