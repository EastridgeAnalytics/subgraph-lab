"""Retract a run from the master graph, over Bolt (page 01: "Push never deletes. Retractions go over Bolt").

`retract(run, confirm=True)` undoes one run's own writes and nothing else:

- it deletes the lab's own nodes whose `run_id` is this run: `Alert`, `RiskCommunity` and `LabRun`, with the
  `ALERT_ON` and `IN_RISK_COMMUNITY` links attached to them;
- it removes every `risk_*` property from the accounts whose `risk_run_id` is this run, and leaves the account.

It never deletes an account, any other customer node or any customer relationship. An account that a push created
because the master graph did not have its key stays, bare. The lab's constraints stay too.

Like `apply`, it refuses unless `confirm is True`, before it opens a connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lab.common.log import get_logger
from lab.common.neo4j import neo4j_session
from lab.contracts.graph import ALERT_LABEL, COMMUNITY_LABEL, LABRUN_LABEL, RISK_PREFIX
from lab.contracts.manifest import StepStatus
from lab.roundtrip.push import PushNotConfirmed
from lab.roundtrip.queries import ident
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("roundtrip.retract")

LAB_LABELS = (ALERT_LABEL, COMMUNITY_LABEL, LABRUN_LABEL)  # the nodes the lab owns; the only ones retract deletes


class RetractNotConfirmed(PushNotConfirmed):
    """`retract` was called without `confirm=True`. Nothing was touched."""


@dataclass
class RetractResult:
    run_id: str
    alerts: int = 0
    communities: int = 0
    lab_runs: int = 0
    accounts_cleaned: int = 0
    properties: list[str] = field(default_factory=list)  # the risk_* names removed


def retract(run: Run, confirm: bool = False) -> RetractResult:
    if confirm is not True:
        raise RetractNotConfirmed(
            f"retract of {run.id} not confirmed: it deletes the run's alerts, communities and LabRun from the master "
            "graph and removes its risk_* properties. Nothing was touched."
        )
    settings = get_settings()
    mapping = run.recipe.graph
    account, key = ident(mapping.account_label), ident(mapping.account_key)
    batch = max(1, settings.push_batch)
    result = RetractResult(run.id)

    with neo4j_session(settings) as session:
        # The accounts: found by `risk_run_id`, which is one of the properties removed, so each batch is final.
        # A retract that stops partway finds only the accounts left when it runs again.
        scope = f"MATCH (a:{account}) WHERE a.risk_run_id = $run"
        keys = [r["k"] for r in session.run(f"{scope} RETURN a.{key} AS k", run=run.id)]
        names = sorted({
            r["n"] for r in session.run(
                f"{scope} UNWIND keys(a) AS n WITH DISTINCT n WHERE n STARTS WITH $prefix RETURN n",
                run=run.id, prefix=RISK_PREFIX,
            )
        })
        if keys:
            statement = (
                f"UNWIND $keys AS k MATCH (a:{account} {{{key}: k}}) REMOVE "
                + ", ".join(f"a.{ident(n)}" for n in names)
            )
            for i in range(0, len(keys), batch):
                part = keys[i : i + batch]
                session.execute_write(lambda tx, p=part: tx.run(statement, keys=p).consume())
        result.accounts_cleaned, result.properties = len(keys), names

        # The lab's own nodes, with the links attached to them, `batch` at a time.
        for label, field_name in ((ALERT_LABEL, "alerts"), (COMMUNITY_LABEL, "communities"),
                                  (LABRUN_LABEL, "lab_runs")):
            statement = (f"MATCH (n:{ident(label)}) WHERE n.run_id = $run WITH n LIMIT {batch} "
                         "DETACH DELETE n RETURN count(*) AS c")
            deleted = 0
            while True:
                n = session.execute_write(lambda tx, q=statement: tx.run(q, run=run.id).single()["c"])
                deleted += n
                if n < batch:
                    break
            setattr(result, field_name, deleted)

    manifest = run.manifest
    manifest.push = []
    manifest.routes.pop("push", None)
    step = manifest.step("push")
    step.status, step.finished_at = StepStatus.PENDING, None
    step.note = (
        f"retracted: {result.alerts} alerts, {result.communities} communities, {result.lab_runs} LabRun, "
        f"risk_* removed from {result.accounts_cleaned} accounts"
    )
    run.save()
    log.info("retracted %s: %s", run.id, step.note)
    return result
