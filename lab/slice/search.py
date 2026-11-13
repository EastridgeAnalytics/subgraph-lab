"""The search slice (page 02): hybrid search picks the starting accounts, hops pick up what is connected.

`search_slice(run, spec)` reads a run store whose first pass is done (decision 31):

1. The query. Text goes to the full-text index first; the vector query is the mean embedding of those hits, because
   Node2Vec embeds structure, not words (decision 33), weighted by score (see `_weighted_mean`). An account uses
   its own embedding and its own summary's words. Given both, each contributes its words and its vector.
2. The hybrid statement from page 02, run in the run store: a vector branch and a full-text branch, each ranked
   on its own, fused as `1 / (60 + rank)`, the top `k`.
3. `hops` hops out from the hits over the payment and identifier relationships, one round at a time in batches
   of `LAB_KEY_BATCH` keys (Relate 1.0.1's `IN $keys` slows with the square of the list).
4. The size, counted the way the adapters will build the slice: its accounts, the identifier nodes those accounts
   link to, the payments between those accounts and their identifier links. Memory comes from `estimate_memory`.

The slice is saved to `artifacts/slice.json` and the manifest's `slice` step is recorded. `current_slice(run)` is what
the models pass as `keys=`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from lab.adapters import reads
from lab.common.log import get_logger
from lab.common.store import estimate_memory
from lab.contracts.api import Hit, SliceEstimate
from lab.contracts.graph import (
    ACCOUNT_TEXT_INDEX,
    ACCOUNT_VECTOR_INDEX,
    EMBEDDING_PROP,
    SUMMARY_PROP,
    GraphMapping,
)
from lab.contracts.manifest import StepStatus
from lab.contracts.recipe import SearchSpec
from lab.roundtrip.queries import ident
from lab.settings import get_settings
from lab.slice.summaries import summary_body

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("slice.search")

SLICE_FILE = "slice.json"
RRF_OFFSET = 60  # page 02: each hit scores 1 / (60 + its rank) in each list, and the scores add up
_WORD = re.compile(r"\w+")


class FirstPassNotRun(RuntimeError):
    """Search is not offered until the first pass has run (page 02)."""


class SliceOverCap(RuntimeError):
    """The saved search slice is bigger than `LAB_SLICE_CAP` nodes: lower `k` or `hops` and search again."""


# ---- the facade ----------------------------------------------------------------------------------------------
def search_slice(run: Run, spec: SearchSpec) -> SliceEstimate:
    """Search, expand, size, save. Raises `FirstPassNotRun` before the first pass. An over-cap slice is saved
    without its keys, marks the `slice` step failed, and `current_slice` then refuses it.
    """
    settings = get_settings()
    run.reload()
    if run.manifest.step("first_pass").status is not StepStatus.DONE:
        raise FirstPassNotRun(f"run {run.id} has no first pass yet: run it, then search (page 02)")
    k = settings.search_k if spec.k is None else spec.k
    hops = settings.search_hops if spec.hops is None else spec.hops
    if k < 1 or hops < 0:
        raise ValueError(f"k must be at least 1 and hops at least 0, got k={k}, hops={hops}")

    run.manifest.start("slice")
    run.save()
    try:
        hits = _hits(run, spec, k)
        accounts, nodes, rels, capped = _expand(run, [h.key for h in hits], hops, settings.slice_cap)
    except Exception as e:
        run.manifest.fail("slice", f"{type(e).__name__}: {e}"[:500])
        run.save()
        raise

    over = nodes > settings.slice_cap
    note = None
    if not hits:
        note = "nothing matched the words and no account was named, so the slice is empty"
    elif over and capped:
        note = (f"stopped expanding past the cap of {settings.slice_cap:,} nodes: the counts are at least these; "
                "lower k or hops")
    elif over:
        note = f"more than the cap of {settings.slice_cap:,} nodes: lower k or hops"
    est = SliceEstimate(
        mode="search", hits=hits, hops=hops, nodes=nodes, relationships=rels,
        memory_bytes=estimate_memory(nodes, rels), over_cap=over, cap=settings.slice_cap, note=note,
    )
    _save(run, spec, k, hops, est, accounts)
    counts = {"hits": len(hits), "hops": hops, "accounts": len(accounts), "nodes": nodes, "relationships": rels,
              "memory_bytes": est.memory_bytes}
    if over:
        run.manifest.fail("slice", note or "over the cap")
        run.manifest.step("slice").counts.update(counts)
    else:
        run.manifest.finish("slice", counts)
    run.manifest.step("slice").note = note  # finish() keeps an old note when given none
    run.save()
    return est


def current_slice(run: Run) -> list[str] | None:
    """The account keys of the saved search slice, for the models' `keys=`. None means the whole run store (no
    search has run). An empty list means the search found nothing. Raises `SliceOverCap` for an over-cap slice.
    """
    path = run.paths.artifacts / SLICE_FILE
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved["estimate"]["over_cap"]:
        est = saved["estimate"]
        raise SliceOverCap(
            f"the saved slice has {est['nodes']:,} nodes, over the cap of {est['cap']:,}: lower k or hops and "
            "search again"
        )
    return saved["keys"]


# ---- the hits ------------------------------------------------------------------------------------------------
def hybrid_statement(g: GraphMapping) -> str:
    """The statement from page 02 over the account label and key of the mapping. Returns `key`, `rrf`, `found_by`."""
    account, key = ident(g.account_label), ident(g.account_key)
    return f"""
CALL () {{
  MATCH (a:{account}) SEARCH a IN (VECTOR INDEX {ACCOUNT_VECTOR_INDEX} FOR $v LIMIT $k) SCORE AS score
  WITH a ORDER BY score DESC WITH collect(a) AS hits
  UNWIND range(0, size(hits) - 1) AS rank
  RETURN hits[rank] AS a, rank, 'vector' AS src
  UNION ALL
  MATCH (a:{account}) SEARCH a IN (FULLTEXT INDEX {ACCOUNT_TEXT_INDEX} FOR $q LIMIT $k) SCORE AS score
  WITH a ORDER BY score DESC WITH collect(a) AS hits
  UNWIND range(0, size(hits) - 1) AS rank
  RETURN hits[rank] AS a, rank, 'fulltext' AS src
}}
WITH a.{key} AS key, sum(1.0 / ({RRF_OFFSET} + rank)) AS rrf, collect(src) AS found_by
ORDER BY rrf DESC, key LIMIT $k
RETURN key, rrf, found_by
"""


def terms(text: str) -> list[str]:
    """The words of `text`, lower-cased, once each. A full-text query made of these cannot be a Lucene operator."""
    return list(dict.fromkeys(_WORD.findall(text.lower())))


def _hits(run: Run, spec: SearchSpec, k: int) -> list[Hit]:
    g = run.recipe.graph
    words: list[str] = []
    vectors: list[np.ndarray] = []
    if spec.text:
        text_words = terms(spec.text)
        if not text_words:
            raise ValueError(f"the search text {spec.text!r} has no words in it")
        words += text_words
        found = _fulltext(run, " ".join(text_words), k)
        embedded = _embeddings(run, list(found))
        if embedded:
            vectors.append(_weighted_mean({key: (embedded[key], found[key]) for key in embedded}))
    if spec.account:
        vector, summary = _account(run, spec.account)
        vectors.append(_unit(vector))
        words += [w for w in terms(summary_body(summary)) if not w.isdigit()]  # the numbers differ per account
    if not vectors:
        return []  # the words matched no account, so there is no vector to search with
    query_vector = _unit(np.mean(vectors, axis=0)).tolist()
    df = run.table(hybrid_statement(g), {"v": query_vector, "q": " ".join(dict.fromkeys(words)), "k": k})
    order = ("vector", "fulltext")
    return [
        Hit(key=str(r["key"]), rrf=float(r["rrf"]), found_by=sorted(set(r["found_by"]), key=order.index))
        for r in df.iter_rows(named=True)
    ]


def _weighted_mean(hits: dict[str, tuple[list[float], float]]) -> np.ndarray:
    """The vector query from the full-text hits (decision 33): the mean of their unit embeddings, each weighted by how
    far its score is above the weakest hit's. Full-text fills its `k` with weak matches that share the strong
    ones' words; an even mean of them lands on the busiest accounts, not on the ones that matched best. When every
    score is the same the mean is even.
    """
    scores = np.array([score for _, score in hits.values()])
    weights = scores - scores.min()
    if not weights.any():
        weights = np.ones_like(weights)
    unit = np.array([_unit(v) for v, _ in hits.values()])
    return _unit(weights @ unit / weights.sum())


def _unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    n = np.linalg.norm(a)
    return a / n if n else a


def _fulltext(run: Run, query: str, k: int) -> dict[str, float]:
    """The full-text hits, best first: account key -> score."""
    g = run.recipe.graph
    df = run.table(
        f"MATCH (a:{ident(g.account_label)}) SEARCH a IN (FULLTEXT INDEX {ACCOUNT_TEXT_INDEX} FOR $q LIMIT $k) "
        f"SCORE AS score RETURN a.{ident(g.account_key)} AS key, score ORDER BY score DESC, key",
        {"q": query, "k": k},
    )
    return {str(key): float(score) for key, score in df.iter_rows()}


def _embeddings(run: Run, keys: list[str]) -> dict[str, list[float]]:
    g = run.recipe.graph
    query = (f"MATCH (a:{ident(g.account_label)}) WHERE a.{ident(g.account_key)} IN $keys "
             f"RETURN a.{ident(g.account_key)} AS key, a.{ident(EMBEDDING_PROP)} AS v")
    out: dict[str, list[float]] = {}
    for batch in reads.key_batches(keys):
        for key, v in run.table(query, {"keys": batch}).iter_rows():
            if v is not None:
                out[str(key)] = v
    return out


def _account(run: Run, key: str) -> tuple[list[float], str]:
    g = run.recipe.graph
    df = run.table(
        f"MATCH (a:{ident(g.account_label)} {{{ident(g.account_key)}: $key}}) "
        f"RETURN a.{ident(EMBEDDING_PROP)} AS v, a.{ident(SUMMARY_PROP)} AS s",
        {"key": key},
    )
    if df.is_empty():
        raise ValueError(f"no account {key!r} in run {run.id}")
    v, s = df.row(0)
    if v is None or s is None:
        raise FirstPassNotRun(f"account {key!r} has no embedding or summary: run the first pass again")
    return v, s


# ---- the hops and the size -----------------------------------------------------------------------------------
@dataclass
class _Frontier:
    accounts: set[str]
    identifiers: set[tuple[str, str]]  # (label, key) of an identifier node


def _expand(run: Run, seeds: list[str], hops: int, cap: int) -> tuple[list[str], int, int, bool]:
    """Accounts within `hops` hops of `seeds`, then the size of what the adapters will build from them.

    A hop crosses a payment (either way) or an identifier link (either way), so an account's other accounts on a
    device are two hops away. Returns the sorted account keys, nodes, relationships, and whether the expansion
    stopped early past `cap`. When it did, the counts are of what was found so far.
    """
    seen = set(seeds)
    seen_ids: set[tuple[str, str]] = set()
    front = _Frontier(set(seeds), set())
    capped = False
    for _ in range(hops):
        if not (front.accounts or front.identifiers):
            break
        links = reads.identifiers(run, sorted(front.accounts))
        new_ids = set(zip(links["label"], links["identifier"], strict=True)) - seen_ids
        new_accounts = (_neighbours(run, front.accounts) | _members(run, front.identifiers)) - seen
        seen |= new_accounts
        seen_ids |= new_ids
        front = _Frontier(new_accounts, new_ids)
        if len(seen) + len(seen_ids) > cap:
            capped = True
            break
    accounts = sorted(seen)
    links = reads.identifiers(run, accounts)
    nodes = len(accounts) + links.select("label", "identifier").unique().height
    rels = links.height + _payments_among(run, accounts)
    return accounts, nodes, rels, capped


def _neighbours(run: Run, keys: set[str]) -> set[str]:
    """Accounts one payment away from `keys`, in either direction."""
    g = run.recipe.graph
    account, key = ident(g.account_label), ident(g.account_key)
    query = (f"MATCH (s:{account})-[:{ident(g.payment_type)}]-(d:{account}) WHERE s.{key} IN $keys "
             f"RETURN DISTINCT d.{key} AS key")
    out: set[str] = set()
    for batch in reads.key_batches(sorted(keys)):
        out.update(str(k) for k in run.table(query, {"keys": batch})["key"].drop_nulls())
    return out


def _members(run: Run, identifiers: set[tuple[str, str]]) -> set[str]:
    """Accounts linked to the identifier nodes `(label, key)`."""
    g = run.recipe.graph
    account = ident(g.account_label)
    out: set[str] = set()
    for link in g.identifiers:
        mine = sorted(k for label, k in identifiers if label == link.label)
        query = (f"MATCH (i:{ident(link.label)})<-[:{ident(link.rel_type)}]-(a:{account}) "
                 f"WHERE i.{ident(link.key)} IN $keys RETURN DISTINCT a.{ident(g.account_key)} AS key")
        for batch in reads.key_batches(mine):
            out.update(str(k) for k in run.table(query, {"keys": batch})["key"].drop_nulls())
    return out


def _payments_among(run: Run, accounts: list[str]) -> int:
    """How many payments go from one of `accounts` to another. Each payment is a relationship."""
    g = run.recipe.graph
    account, key = ident(g.account_label), ident(g.account_key)
    query = (f"MATCH (s:{account})-[t:{ident(g.payment_type)}]->(d:{account}) WHERE s.{key} IN $keys "
             f"RETURN d.{key} AS dst, count(t) AS n")
    frames = [run.table(query, {"keys": batch}) for batch in reads.key_batches(accounts)]
    if not frames:
        return 0
    df = pl.concat(frames, how="vertical_relaxed").with_columns(pl.col("dst").cast(pl.Utf8))
    return int(reads.inside(df, "dst", accounts)["n"].sum() or 0)


# ---- the saved slice -----------------------------------------------------------------------------------------
def _save(run: Run, spec: SearchSpec, k: int, hops: int, est: SliceEstimate, accounts: list[str]) -> None:
    path = run.paths.artifacts / SLICE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "search": spec.model_dump(mode="json"),
        "k": k,
        "hops": hops,
        "estimate": est.model_dump(mode="json"),
        "keys": [] if est.over_cap else accounts,  # an over-cap slice is never handed to a model
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)

