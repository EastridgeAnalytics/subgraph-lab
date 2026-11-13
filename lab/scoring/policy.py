"""The policy (page 04, levels one and two): factors become account scores, and account scores become alerts.

The arithmetic is the `Policy` docstring in `lab/contracts/factors.py`:

- `risk_score_rules` is the weighted mean of the cypher and python factors, `risk_score_model` the weighted mean of
  the model factors, and `risk_score` their mean weighted by each side's total weight.
- Every factor in the file counts. An account a factor says nothing about scores 0 on it, and so does a factor that
  was skipped or failed. Its weight stays in the divisor.
- `risk_factors` is a JSON list of `{"id", "score", "weight"}`, one entry per factor in the file, in file order.
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
from lab.scoring.factors import FactorRun
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
    """
    key = run.recipe.graph.account_key
    factors = factor_run.file.factors
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
    w_rules, w_model = sum(weight[i] for i in rule_ids), sum(weight[i] for i in model_ids)

    def weighted(ids: list[str]) -> pl.Expr:
        return pl.sum_horizontal([cols[i] * weight[i] for i in ids]) if ids else pl.lit(0.0)

    rules_sum, model_sum = weighted(rule_ids), weighted(model_ids)
    total = w_rules + w_model
    wide = wide.with_columns(
        # each side's weighted mean, 0 for a side with no weight
        risk_score_rules=(rules_sum / w_rules) if w_rules > 0 else pl.lit(0.0),
        risk_score_model=(model_sum / w_model) if w_model > 0 else pl.lit(0.0),
        # the two means weighted by each side's total weight, which is the weighted mean over every factor
        risk_score=((rules_sum + model_sum) / total) if total > 0 else pl.lit(0.0),
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
