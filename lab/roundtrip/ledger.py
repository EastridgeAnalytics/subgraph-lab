"""Push bookkeeping on the run manifest: what landed, what did not, what a resume sends (decision 24).

`relate push` has no resume flag, so `manifest.push` is the lab's own ledger: one `PushEntity` per node label and
relationship type in the plan, `landed` once its last batch has gone, plus `LabRun`, which is its own push and
always last (decision 28). A resume pushes the entities that did not land and nothing else. A resent batch is
harmless because the merge is keyed.

What `relate push 1.0.1 --json` really prints (wave 1, probed against Neo4j 2026.08.1):

- On success, exit 0: one JSON object on stdout, `entities[]` with `entity`, `kind` ("node" or "relationship"),
  `total`, `created`, `updated`, `batches`, `skipped` ({dangling, policy, remoteNewer, unkeyed}), `mergeKey`,
  `statement`; and `bytesSent`, `rowsSent`, `statementsSent`. A relationship's `entity` is
  `(Alert)-[:ALERT_ON]->(Account)`.
- On a failed batch, exit 1: the same JSON object is still printed. `batches` counts the batches that completed, so
  an entity landed when it completed every batch it planned. `created` on an entity that did not finish is the plan,
  not what landed. The reason is one line on stderr: `Error: push failed: batch 2 of Alert (rows 1..2) failed: ...`,
  or `Error: push failed: pre-read of Account failed: ...` when the remote could not even be read.
- A dry run (`--dry-run --json`) prints `"dryRun": true` and the plan, with `batches` as the planned count and
  nothing sent.
- `--labels A,B` pushes those nodes and the relationships between them; relationships to any other label are
  reported as one `(dangling)` entity and skipped. `--types T` pushes those relationships and their end nodes, and
  reports those end nodes as node entities, so a node entity in a `--types` push covers only the nodes at the end of
  those relationships, not the whole label.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from lab.contracts.graph import LABRUN_LABEL
from lab.contracts.manifest import PushEntity, RunManifest

LABRUN = LABRUN_LABEL
DANGLING = "(dangling)"  # relate's name for relationships it skipped because an end node was not in the push

_REL = re.compile(r"^\((?P<start>.+?)\)-\[:(?P<type>.+?)\]->\((?P<end>.+)\)$")
_FAILURE = re.compile(
    r"push failed: (?:batch (?P<batch>\d+) of (?P<entity>.+?) \(rows (?P<lo>\d+)\.\.(?P<hi>\d+)\)"
    r"|pre-read of (?P<pre>.+?)) failed: (?P<message>.*)"
)
_CREDENTIALS = re.compile(r"(://)[^/@\s]*@")


@dataclass(frozen=True)
class Failure:
    """The line `relate push` wrote to stderr when it stopped."""

    entity: str | None  # the entity whose batch (or pre-read) failed, when the line names one
    message: str
    batch: int | None = None
    rows: tuple[int, int] | None = None


def clean(text: str, limit: int = 400) -> str:
    """A relate message for the manifest: no credentials in a URL, a bounded length."""
    return _CREDENTIALS.sub(r"\1", text).strip()[:limit]


def parse_failure(error_line: str) -> Failure | None:
    """Read `Error: push failed: batch N of E (rows a..b) failed: ...`. Anything else is kept as a bare message."""
    line = clean(error_line.removeprefix("Error:").strip(), 600)
    if not line:
        return None
    m = _FAILURE.search(line)
    if not m:
        return Failure(None, line[:300])
    entity = m["entity"] or m["pre"]
    rows = (int(m["lo"]), int(m["hi"])) if m["lo"] is not None else None
    where = f"batch {m['batch']} of {entity} (rows {m['lo']}..{m['hi']})" if m["batch"] else f"pre-read of {entity}"
    return Failure(entity, f"{where}: {m['message']}"[:300], int(m["batch"]) if m["batch"] else None, rows)


def rel_type_of(entity: str) -> str | None:
    """`(Alert)-[:ALERT_ON]->(Account)` -> `ALERT_ON`; None for a node label."""
    m = _REL.match(entity)
    return m["type"] if m else None


def parse_report(report: dict | None, *, batch_size: int, returncode: int = 0,
                 failure: Failure | None = None) -> list[PushEntity]:
    """The entity lines of a `relate push --json` report as `PushEntity` rows.

    On a dry run nothing has landed. On a real push, an entity landed when it completed every batch it planned
    (`ceil((total - skipped) / batch_size)`) and is not the one the failure line names; after a clean exit every entity
    landed, except one whose rows were skipped for want of a key. `created` and `updated` are zero for an entity that did
    not land, because the report gives the plan there, not what happened.
    """
    if not report:
        return []
    dry = bool(report.get("dryRun"))
    out = []
    for e in report.get("entities") or []:
        skipped_by = e.get("skipped") or {}
        skipped = sum(int(v) for v in skipped_by.values())
        total, batches = int(e.get("total", 0)), int(e.get("batches", 0))
        planned = math.ceil(max(total - skipped, 0) / max(batch_size, 1))
        name = e.get("entity", "")
        unkeyed = int(skipped_by.get("unkeyed", 0))
        failed_here = failure is not None and failure.entity == name
        landed = (
            not dry and name != DANGLING and not failed_here and not unkeyed and (returncode == 0 or batches >= planned)
        )
        error = None
        if failed_here:
            error = failure.message
        elif unkeyed:
            error = f"{unkeyed} rows skipped: {name} has no merge key in the push store"
        out.append(PushEntity(
            entity=name, kind=e.get("kind", "node"), total=total,
            created=int(e.get("created", 0)) if landed else 0, updated=int(e.get("updated", 0)) if landed else 0,
            skipped=skipped, batches=batches, landed=landed, error=error,
        ))
    return out


def blockers(plan: list[PushEntity]) -> list[str]:
    """Why a plan must not be pushed: relate would skip rows (no key, or a relationship whose end is not in the push)."""
    return [f"{e.entity}: {e.skipped} rows would be skipped" for e in plan if e.skipped or (e.entity == DANGLING and e.total)]


# ---- the ledger on the manifest ----------------------------------------------------------------------------------
def labrun_entity(landed: bool = False, error: str | None = None) -> PushEntity:
    return PushEntity(entity=LABRUN, kind="node", total=1, created=1 if landed else 0, batches=1 if landed else 0,
                      landed=landed, error=error)


def fresh(plan: list[PushEntity]) -> list[PushEntity]:
    """A new push: every planned entity not landed, then `LabRun`."""
    entities = [e.model_copy(update={"landed": False, "created": 0, "updated": 0, "batches": 0, "error": None})
                for e in plan if e.entity != DANGLING]
    return [*entities, labrun_entity()]


def has_pending(entities: list[PushEntity]) -> bool:
    return any(not e.landed for e in entities)


def reconcile(previous: list[PushEntity], plan: list[PushEntity]) -> list[PushEntity]:
    """A resume: keep what landed, take the plan for everything else. An entity the plan no longer has is dropped."""
    landed = {e.entity: e for e in previous if e.landed}
    merged = [landed.get(e.entity) or fresh([e])[0] for e in plan if e.entity != DANGLING]
    merged.append(landed.get(LABRUN) or labrun_entity())
    return merged


def nothing_landed(entities: list[PushEntity]) -> bool:
    """True when no entity but `LabRun` has landed, so one push of every label does the whole job."""
    return not any(e.landed for e in entities if e.entity != LABRUN)


def pending_labels(entities: list[PushEntity]) -> list[str]:
    return [e.entity for e in entities if e.kind == "node" and e.entity != LABRUN and not e.landed]


def pending_types(entities: list[PushEntity]) -> list[str]:
    return [t for e in entities if e.kind == "relationship" and not e.landed and (t := rel_type_of(e.entity))]


def merge(entities: list[PushEntity], reported: list[PushEntity], kinds: tuple[str, ...]) -> list[PushEntity]:
    """Fold a push report into the ledger. Only entities of `kinds` count: a `--types` push also reports the nodes at the
    end of those relationships, which says nothing about whether their whole label landed."""
    by_name = {e.entity: e for e in reported if e.kind in kinds and e.entity != DANGLING}
    return [by_name.get(e.entity, e) for e in entities]


def invalidate(manifest: RunManifest) -> bool:
    """New rows were staged while a push was half done: what landed was a different push, so forget it.

    A finished push stays on the record (the next push of the same run is then a full, idempotent one).
    """
    if has_pending(manifest.push):
        manifest.push = []
        return True
    return False
