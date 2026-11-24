"""Risk factors (page 04, level one): run every entry in the factor file against a run.

`run_factors(run, keys)` reads the factor file (`FactorFile.load`, which is the schema check), runs each factor by
kind and returns one long frame, `factor_id, key, score, evidence`, plus a status per factor.

| Kind | How it runs |
|---|---|
| `cypher` | The `.cypher` file beside the factor file, on the run store, with `$threshold` and `$keys`. The query returns `key`, `value` and `evidence_ids`, and the score is `min(1, value / threshold)`; a query may return `score` itself |
| `python` | `"module:function"`, called as `function(run, factor, keys)`, returning `key`, `score` and optionally `evidence` |
| `model` | `artifacts/models/<factor id>.parquet` in the run, written by the example named in `source`: `key`, `score`, optionally `evidence` |

A factor that cannot run yet is never dropped without a word. It is `skipped` with its reason when its Python
function or its model output is missing, and `failed` with the error when it raised or returned something that
breaks the contract. Either way the policy counts it as 0, the others still run, and the manifest says so.

A recipe can choose its factors (decision 40): `recipe.factors` lists factor ids, and unset means every factor in the
file. A factor the recipe does not list is `not chosen`: it does not run, it is neither skipped nor failed, and the
policy leaves it out of every score (`chosen_factors`). An id the file does not have is an error that names it.

The factor file is the customer's: nothing here names a threshold, a weight or a label.
"""
from __future__ import annotations

import importlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

import polars as pl

from lab.adapters import reads
from lab.common import canonical_json, get_logger
from lab.contracts.factors import FACTOR_RESULT_COLUMNS, Factor, FactorFile
from lab.contracts.recipe import ModelName
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("scoring.factors")

RESULT_SCHEMA = {"factor_id": pl.Utf8, "key": pl.Utf8, "score": pl.Float64, "evidence": pl.Utf8}
_EDGE = 1e-9  # a score this far outside [0, 1] is rounding, not a bad factor

Status = Literal["ran", "skipped", "failed", "not chosen"]


class _Skip(Exception):
    """The factor cannot run yet: its function or its model output is not there."""


@dataclass(frozen=True)
class FactorStatus:
    id: str
    kind: str
    status: Status
    reason: str | None = None
    accounts: int = 0  # accounts the factor said something about
    seconds: float = 0.0


@dataclass
class FactorRun:
    """What `run_factors` returns: the results, and what happened to each factor."""

    path: Path  # the factor file that was read
    file: FactorFile
    results: pl.DataFrame  # factor_id, key, score, evidence; absent accounts are simply not in it
    status: list[FactorStatus]
    keys: list[str] | None = None  # the slice the factors ran on; None is every account in the run store

    @property
    def version(self) -> str:
        return self.file.version

    def with_status(self, *wanted: Status) -> list[FactorStatus]:
        return [s for s in self.status if s.status in wanted]

    @property
    def skipped(self) -> list[FactorStatus]:
        return self.with_status("skipped")

    @property
    def failed(self) -> list[FactorStatus]:
        return self.with_status("failed")

    @property
    def not_chosen(self) -> list[FactorStatus]:
        return self.with_status("not chosen")

    def counts(self) -> dict[str, int]:
        return {
            "factors": len(self.status),
            "factors_ran": len(self.with_status("ran")),
            "factors_skipped": len(self.skipped),
            "factors_failed": len(self.failed),
            "factors_not_chosen": len(self.not_chosen),
            "factor_rows": self.results.height,
        }

    def note(self) -> str | None:
        """For the manifest: the factors that did not run, each with its reason."""
        parts = [f"{s.status} {s.id}: {s.reason}" for s in self.with_status("skipped", "failed")]
        return "; ".join(parts) or None


# ---- the factor file ---------------------------------------------------------------------------------------
def factor_file_path(run: Run) -> Path:
    """The recipe's factor file, else LAB_FACTORS_FILE."""
    return Path(run.recipe.factors_file) if run.recipe.factors_file else get_settings().factors_file


def load_factor_file(run: Run) -> tuple[Path, FactorFile]:
    """The path and the checked contents. A file that breaks the schema raises, naming the entry."""
    path = factor_file_path(run)
    return path, FactorFile.load(path)


def chosen_factors(run: Run, factor_file: FactorFile) -> list[Factor]:
    """The factors this run scores with, in file order: `run.recipe.factors`, or every factor when it is unset.

    An id the file does not have raises a `ValueError` that names it. An empty list chooses nothing.
    """
    wanted = run.recipe.factors
    if wanted is None:
        return list(factor_file.factors)
    have = {f.id for f in factor_file.factors}
    unknown = [i for i in dict.fromkeys(wanted) if i not in have]
    if unknown:
        raise ValueError(f"the recipe chooses factors the factor file does not have: {', '.join(unknown)} "
                         f"(the file has {', '.join(sorted(have)) or 'none'})")
    keep = set(wanted)
    return [f for f in factor_file.factors if f.id in keep]


def empty_results() -> pl.DataFrame:
    return pl.DataFrame(schema=RESULT_SCHEMA)


# ---- run them ----------------------------------------------------------------------------------------------
def run_factors(run: Run, keys: list[str] | None = None) -> FactorRun:
    """The recipe's factors (every factor in the file when it names none), on `keys` (a slice of account keys) or on
    every account in the run store. A factor the recipe does not choose is reported `not chosen` and not run."""
    path, factor_file = load_factor_file(run)
    chosen = {f.id for f in chosen_factors(run, factor_file)}  # raises on an unknown id, before anything runs
    keys = None if keys is None else list(dict.fromkeys(keys))
    frames: list[pl.DataFrame] = []
    status: list[FactorStatus] = []
    for factor in factor_file.factors:
        if factor.id not in chosen:
            status.append(FactorStatus(factor.id, factor.kind, "not chosen", "the recipe does not list it"))
            continue
        started = time.perf_counter()
        try:
            df = _RUNNERS[factor.kind](run, factor, path.parent, keys)
            state, reason = "ran", None
        except _Skip as e:
            df, state, reason = _no_rows(), "skipped", str(e)
        except Exception as e:  # noqa: BLE001 a broken factor must not stop the others
            df, state, reason = _no_rows(), "failed", f"{type(e).__name__}: {e}"
        seconds = time.perf_counter() - started
        if state != "ran":
            log.warning("factor %s %s: %s", factor.id, state, reason)
        status.append(FactorStatus(factor.id, factor.kind, state, reason, df.height, round(seconds, 3)))
        frames.append(df.select(pl.lit(factor.id).alias("factor_id"), *FACTOR_RESULT_COLUMNS))
    results = pl.concat(frames) if frames else empty_results()
    return FactorRun(path=path, file=factor_file, results=results, status=status, keys=keys)


def _no_rows() -> pl.DataFrame:
    return empty_results().select(*FACTOR_RESULT_COLUMNS)


# ---- cypher ------------------------------------------------------------------------------------------------
def _query_text(factor: Factor, base: Path) -> str:
    """The query file, relative to the factor file (page 04's example also writes it relative to the repo)."""
    assert factor.query is not None
    path = base / factor.query
    if not path.exists() and Path(factor.query).exists():
        path = Path(factor.query)
    if not path.exists():
        raise FileNotFoundError(f"query file {factor.query} not found beside {base}")
    return path.read_text(encoding="utf-8")


def run_cypher(run: Run, factor: Factor, base: Path, keys: list[str] | None) -> pl.DataFrame:
    """One Cypher factor on `keys` (or every account), in key batches. The signals reuse it, so a factor and the
    community score never hold two definitions of the same pattern."""
    query = _query_text(factor, base)
    # LAB_KEY_BATCH keys at a time, as the adapters send them (a long IN list is slow in Relate 1.0.1). Each row
    # belongs to the account the query anchors on, so the batches never overlap.
    frames = [run.table(query, {"threshold": factor.threshold, "keys": batch}) for batch in reads.key_batches(keys)]
    if not frames:  # keys == []
        return _no_rows()
    return _cypher_rows(pl.concat(frames, how="vertical_relaxed"), factor)


def _cypher_rows(df: pl.DataFrame, factor: Factor) -> pl.DataFrame:
    """key, value, evidence_ids (or key, score) to key, score, evidence. One row per account.

    An account the query returns more than once keeps its highest value and score, and the union of its evidence ids.
    """
    if "key" not in df.columns or not ({"value", "score"} & set(df.columns)):
        raise ValueError(f"the query returns {df.columns}; it must return key, value and evidence_ids")
    if "score" not in df.columns and not (factor.threshold and factor.threshold > 0):
        raise ValueError("a query that returns value needs a threshold above 0 on the factor")
    merged: dict[str, dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        if row["key"] is None:
            continue
        value, direct = row.get("value"), row.get("score")
        if value is None and direct is None:
            continue
        score = _checked(float(direct)) if direct is not None else min(1.0, float(value) / factor.threshold)
        ids = [str(i) for i in (row.get("evidence_ids") or []) if i is not None]
        entry = merged.setdefault(str(row["key"]), {"score": 0.0, "value": None, "ids": []})
        entry["score"] = max(entry["score"], score)
        if value is not None:
            entry["value"] = value if entry["value"] is None else max(entry["value"], value)
        entry["ids"] += [i for i in ids if i not in entry["ids"]]
    out = {"key": [], "score": [], "evidence": []}
    for key in sorted(merged):
        e = merged[key]
        body: dict[str, Any] = {"evidence_ids": e["ids"]}
        if e["value"] is not None:
            body["value"] = e["value"]
        if factor.threshold is not None:
            body["threshold"] = factor.threshold
        out["key"].append(key)
        out["score"].append(e["score"])
        out["evidence"].append(canonical_json(body))
    return pl.DataFrame(out, schema={c: RESULT_SCHEMA[c] for c in FACTOR_RESULT_COLUMNS})


def _checked(score: float) -> float:
    """A score in [0, 1], or an error. Rounding past the edge is clipped."""
    if math.isnan(score) or score < -_EDGE or score > 1 + _EDGE:
        raise ValueError(f"a score must be in [0, 1], got {score}")
    return min(1.0, max(0.0, score))


# ---- python ------------------------------------------------------------------------------------------------
def _load_function(spec: str):
    module, sep, name = spec.partition(":")
    if not (sep and module and name):
        raise ValueError(f"function {spec!r} must be 'module.path:function'")
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name and (module == e.name or module.startswith(e.name + ".")):
            raise _Skip(f"module {module} not found") from e
        raise  # the module is there and needs something that is not: a real failure
    fn = getattr(mod, name, None)
    if not callable(fn):
        raise _Skip(f"module {module} has no function {name}")
    return fn


def _run_python(run: Run, factor: Factor, base: Path, keys: list[str] | None) -> pl.DataFrame:
    assert factor.function is not None
    fn = _load_function(factor.function)
    return _result_frame(fn(run, factor, keys), keys, {"function": factor.function})


# ---- model -------------------------------------------------------------------------------------------------
def model_output_path(run: Run, factor: Factor) -> Path:
    return run.paths.artifacts / "models" / f"{factor.id}.parquet"


def _run_model(run: Run, factor: Factor, base: Path, keys: list[str] | None) -> pl.DataFrame:
    path = model_output_path(run, factor)
    if not path.exists():
        model = Path(factor.source or "").name  # examples/tgn writes the tgn model's output
        if model in get_args(ModelName) and model not in run.recipe.models:
            raise _Skip(f"the {model} model was not selected for this run, so it wrote no output")
        raise _Skip(f"no model output at {path.relative_to(run.paths.root).as_posix()}; run {factor.source} first")
    return _result_frame(pl.read_parquet(path), keys, {"source": factor.source})


# ---- what a python or model factor hands back ---------------------------------------------------------------
def _result_frame(df: Any, keys: list[str] | None, default_evidence: dict[str, Any]) -> pl.DataFrame:
    """Check a python or model result against the contract and return key, score, evidence, one row per account.

    Keys become strings, scores must be in [0, 1], evidence becomes valid canonical JSON (text that is not JSON is
    kept as {"text": ...}; none becomes `default_evidence`). Accounts outside the slice are dropped. An account
    that appears twice keeps its highest score.
    """
    if not isinstance(df, pl.DataFrame):
        raise TypeError(f"a factor must return a Polars DataFrame, got {type(df).__name__}")
    missing = [c for c in ("key", "score") if c not in df.columns]
    if missing:
        raise ValueError(f"the result has no {missing} column; it must have key, score and optionally evidence")
    has_evidence = "evidence" in df.columns
    df = df.select(
        pl.col("key").cast(pl.Utf8),
        pl.col("score").cast(pl.Float64),
        (pl.col("evidence").cast(pl.Utf8) if has_evidence else pl.lit(None, dtype=pl.Utf8).alias("evidence")),
    ).drop_nulls("key")
    if df["score"].null_count():
        raise ValueError("the result has accounts with no score")
    scores = [_checked(s) for s in df["score"].to_list()]
    default = canonical_json(default_evidence)
    evidence = [_evidence_json(e, default) for e in df["evidence"].to_list()]
    df = pl.DataFrame({"key": df["key"], "score": scores, "evidence": evidence}, schema={
        c: RESULT_SCHEMA[c] for c in FACTOR_RESULT_COLUMNS})
    df = df.sort("score", "key", descending=[True, False]).unique("key", keep="first", maintain_order=True)
    return reads.inside(df, "key", keys).sort("key")


def _evidence_json(text: str | None, default: str) -> str:
    if text is None:
        return default
    try:
        return canonical_json(json.loads(text))
    except ValueError:
        return canonical_json({"text": text})


_RUNNERS = {"cypher": run_cypher, "python": _run_python, "model": _run_model}
