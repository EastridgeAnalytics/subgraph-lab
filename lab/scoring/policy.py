"""The policy (page 04, levels one and two): factors become account scores, and account scores become alerts.

`policy.combine` says how (decision 39). The factors that count are the recipe's (`chosen_factors`: every factor in
the file unless `recipe.factors` lists some). Each side is its own combination, and `risk_score` is all of them:

- `risk_score_rules` combines the cypher and python factors, `risk_score_model` the model factors.
- `noisy_or`, the shipped policy: `1 - Π(1 - wᵢ·sᵢ)`, where `wᵢ` is how much factor i alone can say and `sᵢ` is its
  score. A factor that did not fire, was skipped or failed has `sᵢ` of 0 and changes nothing; a new factor can only
  add. Every weight must be in [0, 1]: a weight outside it is an error that names the factor, never clipped.
  `risk_score` equals `1 - (1 - risk_score_rules)(1 - risk_score_model)`.
- `weighted_mean`, for anyone who sets it: the weighted mean of each side, and `risk_score` the two means weighted by
  each side's total weight. An account a factor says nothing about scores 0 on it, and so does a factor that was
  skipped or failed. Its weight stays in the divisor.
- `risk_factors` is a JSON list of `{"id", "score", "weight"}`, one entry per chosen factor, in file order.
- An account at or above `alert_threshold` gets an alert keyed `alert_key(run.id, account_key, window)`.

The weights and the threshold are in `factors/factors.yaml` and `factors/policy.yaml`, never here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from lab.adapters import reads
from lab.common import alert_key, canonical_json
from lab.contracts.factors import Policy
from lab.scoring.factors import FactorRun, chosen_factors
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

_EDGE = 1e-9  # float noise: 0.5999999999999999 still reaches a threshold of 0.6


def policy_file_path(run: Run) -> Path:
    """The recipe's policy file, else LAB_POLICY_FILE."""
    return Path(run.recipe.policy_file) if run.recipe.policy_file else get_settings().policy_file


def load_policy(run: Run) -> Policy:
    return Policy.load(policy_file_path(run))


def account_scores(run: Run, factor_run: FactorRun, policy: Policy, accounts: list[str] | None = None) -> pl.DataFrame:
    """One row per account: the key (named as the mapping's account key) and the four `risk_*` score columns.

    `accounts` is who gets a row. The default is every account of the factor run's slice in the run store, so an
    account that no factor fired on still gets its 0. A factor row for an account that is not in that list is
    dropped, never added: a staged key that is not in the master graph would create an account there.

    Only the recipe's chosen factors count (`chosen_factors`); under `noisy_or` each of their weights must be in [0, 1].
    """
    key = run.recipe.graph.account_key
    factors = chosen_factors(run, factor_run.file)
    if policy.combine == "noisy_or":
        too_big = [f"{f.id} ({f.weight:g})" for f in factors if f.weight > 1]
        if too_big:
            raise ValueError(f"combine: noisy_or needs every weight in [0, 1]; out of range: {', '.join(too_big)}")
    if accounts is None:
        accounts = reads.accounts(run, factor_run.keys)["key"].to_list()
    wide = pl.DataFrame({"__key": pl.Series(list(dict.fromkeys(accounts)), dtype=pl.Utf8)})
    for f in factors:
        scored = factor_run.results.filter(pl.col("factor_id") == f.id).select(
            pl.col("key").alias("__key"), pl.col("score").cast(pl.Float64).alias(f"__{f.id}"))
        wide = wide.join(scored, on="__key", how="left")
    cols = {f.id: pl.col(f"__{f.id}").fill_null(0.0) for f in factors}

    rule_ids = [f.id for f in factors if f.kind != "model"]
    model_ids = [f.id for f in factors if f.kind == "model"]
    weight = {f.id: f.weight for f in factors}
    combine = _noisy_or if policy.combine == "noisy_or" else _weighted_mean
    wide = wide.with_columns(
        risk_score_rules=combine(cols, weight, rule_ids),
        risk_score_model=combine(cols, weight, model_ids),
        risk_score=combine(cols, weight, rule_ids + model_ids),
    )
    ids, weights = list(weight), list(weight.values())
    columns = [wide[f"__{i}"].fill_null(0.0).to_list() for i in ids]
    risk_factors = [
        canonical_json([{"id": i, "score": s, "weight": w} for i, w, s in zip(ids, weights, row, strict=True)])
        for row in zip(*columns, strict=True)
    ] if ids else ["[]"] * wide.height
    return wide.select(
        pl.col("__key").alias(key),
        pl.col("risk_score").cast(pl.Float64),
        pl.col("risk_score_model").cast(pl.Float64),
        pl.col("risk_score_rules").cast(pl.Float64),
        pl.Series("risk_factors", risk_factors, dtype=pl.Utf8),
    ).sort(pl.col("risk_score"), pl.col(key), descending=[True, False])


def _noisy_or(cols: dict[str, pl.Expr], weight: dict[str, float], ids: list[str]) -> pl.Expr:
    """1 - Π(1 - w·s) over `ids`: each factor's weight is how much it alone can say. No factors is 0."""
    left = pl.lit(1.0)
    for i in ids:
        left = left * (1.0 - cols[i].clip(0.0, 1.0) * weight[i])
    return (1.0 - left).clip(0.0, 1.0)


def _weighted_mean(cols: dict[str, pl.Expr], weight: dict[str, float], ids: list[str]) -> pl.Expr:
    """Σ w·s / Σ w over `ids`, a factor that did not fire counting 0. No weight is 0."""
    total = sum(weight[i] for i in ids)
    if total <= 0:
        return pl.lit(0.0)
    return pl.sum_horizontal([cols[i] * weight[i] for i in ids]) / total


def alerts(run: Run, scores: pl.DataFrame, policy: Policy, factor_run: FactorRun | None = None) -> pl.DataFrame:
    """One alert per account at or above `policy.alert_threshold`, strongest first.

    Columns: `alert_key`, `account_key`, `window`, `score`, `evidence`. The evidence is JSON: the account's score
    and its two parts, and the factors that fired (score above 0), each with its weight and its own evidence, the
    ids included. Give `factor_run` for that evidence; without it the factors carry only id, score and weight.
    """
    key = run.recipe.graph.account_key
    hit = scores.filter(pl.col("risk_score") >= policy.alert_threshold - _EDGE)
    names: dict[str, dict[str, str]] = {}
    evidence_by: dict[tuple[str, str], object] = {}
    if factor_run is not None:
        names = {f.id: {"name": f.name, "kind": f.kind} for f in factor_run.file.factors}
        evidence_by = _evidence_of(factor_run, hit[key].to_list())
    rows = []
    for r in hit.iter_rows(named=True):
        account = str(r[key])
        fired = []
        for f in json.loads(r["risk_factors"]):
            if f["score"] > 0:
                extra = {"evidence": evidence_by.get((f["id"], account), {})} if factor_run is not None else {}
                fired.append({**f, **names.get(f["id"], {}), **extra})
        fired.sort(key=lambda e: (-e["score"] * e["weight"], e["id"]))
        evidence = {
            "account_key": account, "score": r["risk_score"], "alert_threshold": policy.alert_threshold,
            "window": policy.window, "risk_score_rules": r["risk_score_rules"],
            "risk_score_model": r["risk_score_model"], "factors": fired,
        }
        rows.append({
            "alert_key": alert_key(run.id, account, policy.window), "account_key": account, "window": policy.window,
            "score": r["risk_score"], "evidence": canonical_json(evidence),
        })
    return pl.DataFrame(rows, schema={
        "alert_key": pl.Utf8, "account_key": pl.Utf8, "window": pl.Utf8, "score": pl.Float64, "evidence": pl.Utf8,
    }).sort(pl.col("score"), pl.col("account_key"), descending=[True, False])


def _evidence_of(factor_run: FactorRun, accounts: list[str]) -> dict[tuple[str, str], object]:
    """(factor id, account) to the factor's evidence, parsed, for these accounts."""
    r = factor_run.results.filter(pl.col("key").is_in(accounts))
    return {(f, k): json.loads(e) for f, k, e in zip(r["factor_id"], r["key"], r["evidence"], strict=True)}
