"""The way out, steps two and three: review, then push (page 01, "Review" and "Push"; decisions 22, 24, 28 and 32).

`preview(run)` shows what a push would do: `relate push --dry-run` parsed into `PushEntity` rows, the same dry run as
relate prints it, and the lab's own delta read from the master graph. It writes nothing anywhere but the manifest.

`apply(run, confirm)` is the only code path that writes to the master graph (the other is `retract`, and the Bolt
fallback below is inside `apply`). It refuses unless `confirm is True`, as its first act. With a confirm it

1. runs the dry run again, always, and stops before any write if the plan would skip rows;
2. makes sure the master graph has uniqueness constraints on the lab's OWN labels (`Alert`, `RiskCommunity`,
   `LabRun`), `IF NOT EXISTS`, over Bolt (decision 32). It never creates or changes a constraint on a customer label;
3. runs `relate push` from the push store, never the run store, keyed, in batches of `push_batch`;
4. writes `LabRun` last, as its own push (decision 28). If it is on the master graph, the run landed whole.

A push that stops partway records which entities landed (`manifest.push`), fails the `push` step and returns
`PushResult(pushed=False)`; it does not raise. The next `apply(confirm=True)` pushes only what did not land and says
`resumed=True` (decision 24). When, and only when, `unreachable_reason` says the Query API cannot be reached, the same
rows go over Bolt instead with `UNWIND ... MERGE ... SET +=`, and the fallback is recorded with its reason.

`relate push` reads the store, so the lab's writable connection to the push store is closed before every call
(`storage locked` otherwise) and reopens on next use.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from lab.common.ids import iso_utc
from lab.common.log import get_logger
from lab.common.neo4j import neo4j_session
from lab.common.relate_cli import RelateError, credentials_file, plaintext_flag, relate_cli
from lab.common.store import rows
from lab.contracts.api import PushResult, Review
from lab.contracts.graph import (
    ALERT_KEY,
    ALERT_LABEL,
    COMMUNITY_KEY,
    COMMUNITY_LABEL,
    LABRUN_KEY,
    LABRUN_LABEL,
)
from lab.contracts.manifest import PushEntity, Route, StepStatus, now_iso
from lab.roundtrip import ledger
from lab.roundtrip.delta import DeltaError, compute, read_remote
from lab.roundtrip.indexes import unique_constraint
from lab.roundtrip.pull import unreachable_reason
from lab.roundtrip.queries import ident
from lab.roundtrip.stage import (
    NodeShape,
    NothingStaged,
    RelShape,
    ensure_push_constraints,
    has_staged,
    node_shapes,
    push_store_counts,
    rel_shapes,
)
from lab.settings import Settings, get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("roundtrip.push")

# The only constraints the lab ever creates on the master graph: its own labels (decision 32). A customer label never
# appears here, and nothing else in this package runs CREATE or DROP on the master graph's schema.
OWN_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    (ALERT_LABEL, ALERT_KEY),
    (COMMUNITY_LABEL, COMMUNITY_KEY),
    (LABRUN_LABEL, LABRUN_KEY),
)


class PushError(RuntimeError):
    """Something stopped a review or a push before it could say anything more useful."""


class PushNotConfirmed(RuntimeError):
    """`apply` was called without `confirm=True`. Nothing was touched."""


# ---- the master graph's own constraints (decision 32) ------------------------------------------------------------
def own_constraints_missing(session) -> list[tuple[str, str]]:
    """Which of the lab's own uniqueness constraints the master graph lacks. A read."""
    have = {
        (r["labelsOrTypes"][0], r["properties"][0])
        for r in read_remote(
            session, "SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, properties "
                     "WHERE entityType = 'NODE' AND type IN ['UNIQUENESS', 'NODE_KEY'] "
                     "RETURN labelsOrTypes, properties"
        )
        if len(r["labelsOrTypes"]) == 1 and len(r["properties"]) == 1
    }
    return [c for c in OWN_CONSTRAINTS if c not in have]


def ensure_master_constraints(settings: Settings | None = None) -> list[tuple[str, str]]:
    """Create the lab's own constraints on the master graph where missing. Returns the ones it created.

    Called only by `apply` after a confirm. Loops over `OWN_CONSTRAINTS` and nothing else.
    """
    try:
        with neo4j_session(settings) as session:
            missing = own_constraints_missing(session)
            for label, key in missing:
                session.run(unique_constraint(label, key)).consume()
            return missing
    except Exception as e:
        raise PushError(
            f"could not make the lab's own uniqueness constraints on the master graph over Bolt: "
            f"{type(e).__name__}: {e}"[:400]
        ) from e


# ---- relate -----------------------------------------------------------------------------------------------------
def _args(run: Run, settings: Settings, creds, selector: list[str], *, dry_run: bool = False,
          as_json: bool = True) -> list[str]:
    args = ["push", "--to", str(creds), "--database", str(run.paths.push_store.resolve()), *selector,
            "--batch-size", str(settings.push_batch)]
    if dry_run:
        args.append("--dry-run")
    if as_json:
        args.append("--json")
    return [*args, *plaintext_flag(settings)]


def _all_labels(run: Run) -> list[str]:
    return [shape.label for shape in node_shapes(run.recipe.graph)]


def _dry_run(run: Run, settings: Settings, with_text: bool) -> tuple[list[PushEntity], str]:
    """`relate push --dry-run --json` parsed, and (when asked) the same dry run as relate prints it. Never contacts the remote."""
    run.close()  # relate reads the push store; the lab's writer would lock it
    selector = ["--labels", ",".join(_all_labels(run))]
    with credentials_file(settings) as creds:
        j = relate_cli(_args(run, settings, creds, selector, dry_run=True), check=False, settings=settings)
        t = (relate_cli(_args(run, settings, creds, selector, dry_run=True, as_json=False), check=False,
                        settings=settings) if with_text else None)
    for result in (j, t):
        if result is not None and result.returncode != 0:
            raise PushError(f"relate push --dry-run failed: {ledger.clean(result.error_line())}")
    report = j.json if isinstance(j.json, dict) else None
    if report is None:
        raise PushError("relate push --dry-run --json printed no report")
    return ledger.parse_report(report, batch_size=settings.push_batch), (t.stdout.strip() if t else "")


def _staged_counts(run: Run) -> dict[str, int]:
    if not run.paths.push_store.exists():
        raise NothingStaged(f"nothing is staged for {run.id}: stage a results table first")
    counts = push_store_counts(run.push_store(), run.recipe.graph)
    if not has_staged(counts):
        raise NothingStaged(f"nothing is staged for {run.id}: stage a results table first")
    return counts


# ---- review -----------------------------------------------------------------------------------------------------
def preview(run: Run) -> Review:
    """The dry run plus the delta, for a person to read before anything is sent. Writes nothing to the master graph.

    Raises `NothingStaged` when the push store is empty, `DeltaError` when the master graph cannot be read over Bolt
    (a delta nobody can see is not a review), `PushError` when `relate` fails.
    """
    settings = get_settings()
    _staged_counts(run)
    manifest = run.manifest
    manifest.start("review")
    run.save()
    try:
        with neo4j_session(settings) as session:
            try:
                delta = compute(run, session=session)
                missing_constraints = own_constraints_missing(session)
            except DeltaError:
                raise
            except Exception as e:
                raise DeltaError(f"could not read the master graph over Bolt: {type(e).__name__}: {e}"[:400]) from e
        plan, text = _dry_run(run, settings, with_text=True)
    except Exception as e:
        manifest.fail("review", f"{type(e).__name__}: {e}"[:500])
        run.save()
        raise

    notes = [_constraint_note(missing_constraints)]
    if delta.missing_remote_keys:
        notes.append(
            f"Warning: {delta.missing_remote_keys} of {delta.sampled} sampled accounts are not on the master graph. "
            f"A keyed merge creates them, with only their risk_* properties."
        )
    if ledger.has_pending(manifest.push):
        landed = [e.entity for e in manifest.push if e.landed]
        waiting = [e.entity for e in manifest.push if not e.landed]
        notes.append(
            f"An earlier push of this run stopped. Landed: {', '.join(landed) or 'nothing'}. Not landed: "
            f"{', '.join(waiting)}. Applying resumes it and pushes only what did not land."
        )
    plan_text = text + "\n\n-- added by the lab, not printed by relate --\n" + "\n".join(notes) + "\n"
    manifest.finish("review", {
        "entities": len(plan), "rows": sum(e.total for e in plan), "new_alerts": delta.new_alerts,
        "new_communities": delta.new_communities, "missing_remote_keys": delta.missing_remote_keys,
        "sampled": delta.sampled,
    })
    run.save()
    return Review(run_id=run.id, plan=plan, plan_text=plan_text, delta=delta)


def _constraint_note(missing: list[tuple[str, str]]) -> str:
    if not missing:
        return "The master graph already has the uniqueness constraints on the lab's own labels (Alert, RiskCommunity, LabRun)."
    names = ", ".join(f"{label}.{key}" for label, key in missing)
    return (
        f"Before any row is sent, the lab will create these uniqueness constraints on the master graph, over Bolt, "
        f"IF NOT EXISTS: {names}. They are on the lab's own labels only; no customer label gets a constraint."
    )


# ---- push -------------------------------------------------------------------------------------------------------
def apply(run: Run, confirm: bool = False) -> PushResult:
    """Push the run's push store to the master graph. The only function that writes there, besides `retract`.

    Raises `PushNotConfirmed` unless `confirm is True` (nothing else has run by then) and `NothingStaged` for an empty
    push store. Anything that goes wrong after that comes back as `PushResult(pushed=False, message=...)`.
    """
    if confirm is not True:
        raise PushNotConfirmed(
            f"push of {run.id} not confirmed: apply needs confirm=True after a person has seen the review. "
            "Nothing was sent."
        )
    settings = get_settings()
    counts = _staged_counts(run)
    manifest = run.manifest
    resumed = ledger.has_pending(manifest.push)
    reviewed = manifest.step("review").status is StepStatus.DONE  # recorded, not required: the API layer gates on it
    manifest.start("push")
    run.save()
    try:
        ok, message = _apply(run, settings, counts, resumed)
    except Exception as e:
        log.exception("push of %s failed", run.id)
        ok, message = False, f"{type(e).__name__}: {e}"[:500]
    finally:
        run.close()
    if not ok:
        summary = _stopped_summary(manifest.push)
        manifest.fail("push", message[:500])
        run.save()
        return PushResult(run_id=run.id, pushed=False, entities=list(manifest.push), resumed=resumed,
                          message=f"Push stopped: {message} {summary}".strip())
    route = manifest.routes.get("push", Route.RELATE)
    sent = sum(e.total for e in manifest.push)
    manifest.finish(
        "push",
        {"entities": len(manifest.push), "rows": sent, "created": sum(e.created for e in manifest.push),
         "updated": sum(e.updated for e in manifest.push), "resumed": int(resumed), "reviewed": int(reviewed)},
        note=f"route {route.value}; " + ("review on record" if reviewed else "no review on record"),
    )
    manifest.status, manifest.finished_at = StepStatus.DONE, now_iso()
    run.save()
    what = "Resumed and finished the push" if resumed else "Pushed"
    return PushResult(
        run_id=run.id, pushed=True, entities=list(manifest.push), resumed=resumed,
        message=f"{what}: {sent} rows in {len(manifest.push)} entities by {route.value}; LabRun {run.id} written last.",
    )


def _stopped_summary(entities: list[PushEntity]) -> str:
    landed = [e.entity for e in entities if e.landed]
    waiting = [e.entity for e in entities if not e.landed]
    return (f"Landed: {', '.join(landed) or 'nothing'}. Not landed: {', '.join(waiting) or 'nothing'}. "
            "Apply again with confirm to resume.")


def _apply(run: Run, settings: Settings, counts: dict[str, int], resumed: bool) -> tuple[bool, str]:
    manifest = run.manifest
    plan, _ = _dry_run(run, settings, with_text=False)  # every push follows a dry run, even one nobody asked for
    if bad := ledger.blockers(plan):
        return False, "the dry run shows rows the push would skip, so nothing was sent: " + "; ".join(bad) + "."
    manifest.push = ledger.reconcile(manifest.push, plan) if resumed else ledger.fresh(plan)
    if not resumed or "push" not in manifest.routes:
        manifest.routes["push"] = Route.RELATE  # a fallback below says otherwise; a resume keeps an earlier fallback
    run.save()

    created = ensure_master_constraints(settings)
    if created:
        log.info("created the lab's own constraints on the master graph: %s", created)

    ok, message, bolt = _push_entities(run, settings)
    if not ok:
        return False, message
    return _push_labrun(run, settings, counts, bolt)


def _status(result, reported: list[PushEntity], kinds: tuple[str, ...], args: list[str]) -> tuple[str, str]:
    """`ok`, `failed` or `unreachable`, and why, for one relate push."""
    if result.returncode == 0:
        short = [e for e in reported if e.kind in kinds and e.entity != ledger.DANGLING and not e.landed]
        if short:
            return "failed", "relate skipped rows: " + "; ".join(e.error or e.entity for e in short)
        return "ok", ""
    reason = unreachable_reason(RelateError(args, result))
    if reason:
        return "unreachable", reason
    failure = ledger.parse_failure(result.error_line())
    return "failed", "relate push stopped: " + (failure.message if failure else f"exit {result.returncode}")


def _relate_call(run: Run, settings: Settings, creds, flag: str, names: list[str],
                 kinds: tuple[str, ...]) -> tuple[str, str]:
    """One `relate push` of `--labels` or `--types`, folded into the ledger."""
    args = _args(run, settings, creds, [f"--{flag}", ",".join(names)])
    run.close()
    result = relate_cli(args, check=False, settings=settings)
    failure = ledger.parse_failure(result.error_line()) if result.returncode else None
    report = result.json if isinstance(result.json, dict) else None
    reported = ledger.parse_report(report, batch_size=settings.push_batch, returncode=result.returncode,
                                   failure=failure)
    run.manifest.push = ledger.merge(run.manifest.push, reported, kinds)
    run.save()
    return _status(result, reported, kinds, args)


def _push_entities(run: Run, settings: Settings) -> tuple[bool, str, bool]:
    """Everything but `LabRun`: all labels in one push when nothing has landed, else only what did not.

    Returns whether it worked, why not, and whether the Bolt fallback carried it."""
    manifest = run.manifest
    calls: list[tuple[str, list[str], tuple[str, ...]]] = []
    if ledger.nothing_landed(manifest.push):
        calls.append(("labels", _all_labels(run), ("node", "relationship")))
    else:
        if labels := ledger.pending_labels(manifest.push):
            calls.append(("labels", labels, ("node",)))
        if types := ledger.pending_types(manifest.push):
            calls.append(("types", types, ("relationship",)))
    with credentials_file(settings) as creds:
        for flag, names, kinds in calls:
            status, text = _relate_call(run, settings, creds, flag, names, kinds)
            if status == "unreachable":
                ok, message = _bolt_push(run, settings, text)
                return ok, message, True
            if status == "failed":
                return False, text, False
    return True, "", False


# ---- LabRun, last -----------------------------------------------------------------------------------------------
def _iso(text: str | None) -> str | None:
    if not text:
        return None
    return iso_utc(datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC))


def _labrun_props(run: Run, counts: dict[str, int], route: Route) -> dict[str, Any]:
    """The `LabRun` node's properties, without `finished_at`. A value that is None is left out."""
    m = run.manifest
    started = min((s.started_at for s in m.steps if s.started_at), default=None) or m.created_at
    props = {
        "started_at": _iso(started), "recipe_hash": m.recipe_hash, "model_hash": m.model_hash,
        "factor_version": m.factor_version,
        "route_pull": m.routes["pull"].value if "pull" in m.routes else None, "route_push": route.value,
        "counts": json.dumps(counts, sort_keys=True, separators=(",", ":")),
    }
    return {k: v for k, v in props.items() if v is not None}


def _write_labrun(run: Run, counts: dict[str, int], route: Route) -> dict[str, Any]:
    """Put the `LabRun` node in the push store, and close the store so relate can read it.

    A push of the same, unchanged run keeps the `finished_at` of the first one, so pushing twice changes nothing.
    """
    conn = run.push_store()
    ensure_push_constraints(conn, run.recipe.graph)
    props = _labrun_props(run, counts, route)
    old = rows(conn, f"MATCH (l:{ident(LABRUN_LABEL)} {{{ident(LABRUN_KEY)}: $id}}) RETURN properties(l) AS p",
               {"id": run.id})
    same = old and {k: v for k, v in old[0]["p"].items() if k not in ("finished_at", LABRUN_KEY)} == props
    props["finished_at"] = old[0]["p"]["finished_at"] if same and "finished_at" in old[0]["p"] else iso_utc()
    conn.execute(
        f"MERGE (l:{ident(LABRUN_LABEL)} {{{ident(LABRUN_KEY)}: $id}}) SET l += $props", {"id": run.id, "props": props}
    )
    run.close()
    return props


def _push_labrun(run: Run, settings: Settings, counts: dict[str, int], bolt: bool) -> tuple[bool, str]:
    """`LabRun` is its own push, after everything else. Over Bolt when this push already fell back to it."""
    manifest = run.manifest
    route = Route.BOLT if bolt else Route.RELATE
    props = _write_labrun(run, counts, manifest.routes.get("push", route))
    if route is Route.RELATE:
        with credentials_file(settings) as creds:
            status, text = _relate_call(run, settings, creds, "labels", [ledger.LABRUN], ("node",))
        if status == "failed":
            return False, text
        if status == "unreachable":
            manifest.fallback("push", Route.BOLT, text)
            run.save()
            route = Route.BOLT
            props = _write_labrun(run, counts, Route.BOLT)
    if route is Route.BOLT:
        try:
            with neo4j_session(settings) as session:
                _bolt_labrun(session, run.id, props)
        except Exception as e:
            manifest.push = ledger.merge(
                manifest.push, [ledger.labrun_entity(error=f"{type(e).__name__}: {e}"[:300])], ("node",)
            )
            return False, f"LabRun did not reach the master graph over Bolt: {type(e).__name__}: {e}"[:300]
        manifest.push = ledger.merge(manifest.push, [ledger.labrun_entity(landed=True)], ("node",))
    run.save()
    return True, ""


# ---- the Bolt fallback --------------------------------------------------------------------------------------------
def _bolt_push(run: Run, settings: Settings, reason: str) -> tuple[bool, str]:
    """The Query API cannot be reached: write what has not landed over Bolt, the same keys and the same MERGE."""
    manifest = run.manifest
    manifest.fallback("push", Route.BOLT, reason)
    run.save()
    log.warning("falling back to Bolt writes: %s", reason)
    mapping = run.recipe.graph
    labels, types = ledger.pending_labels(manifest.push), ledger.pending_types(manifest.push)
    conn = run.push_store()
    current = ""
    try:
        with neo4j_session(settings) as session:
            for shape in node_shapes(mapping):
                if shape.label in labels:
                    current = shape.label
                    entity = _bolt_nodes(session, conn, shape, settings.push_batch)
                    manifest.push = ledger.merge(manifest.push, [entity], ("node",))
                    run.save()
            for rel in rel_shapes(mapping):
                if rel.type in types:
                    current = rel.entity
                    entity = _bolt_rels(session, conn, rel, settings.push_batch)
                    manifest.push = ledger.merge(manifest.push, [entity], ("relationship",))
                    run.save()
    except Exception as e:
        message = f"Bolt fallback stopped at {current}: {type(e).__name__}: {e}"[:400]
        log.warning(message)
        return False, message
    finally:
        run.close()
    return True, ""


def _bolt_nodes(session, conn, shape: NodeShape, batch: int) -> PushEntity:
    """Every node of one label in the push store, MERGEd on its key in batches, read page by page by key."""
    label, key = ident(shape.label), ident(shape.key)
    statement = f"UNWIND $rows AS r MERGE (n:{label} {{{key}: r.key}}) SET n += r.props"
    total = created = batches = 0
    last = None
    while True:
        where = f"WHERE n.{key} > $last " if last is not None else ""
        page = rows(
            conn, f"MATCH (n:{label}) {where}RETURN n.{key} AS key, properties(n) AS props ORDER BY key LIMIT {int(batch)}",
            {"last": last} if last is not None else {},
        )
        if not page:
            break
        data = [{"key": r["key"], "props": {k: v for k, v in r["props"].items() if k != shape.key}} for r in page]
        summary = session.execute_write(lambda tx, d=data: tx.run(statement, rows=d).consume())
        created += summary.counters.nodes_created
        total, batches, last = total + len(data), batches + 1, page[-1]["key"]
        if len(page) < batch:
            break
    return PushEntity(entity=shape.label, kind="node", total=total, created=created, updated=total - created,
                      batches=batches, landed=True)


def _bolt_rels(session, conn, rel: RelShape, batch: int) -> PushEntity:
    s, e = rel.start, rel.end
    pairs = rows(
        conn,
        f"MATCH (s:{ident(s.label)})-[:{ident(rel.type)}]->(e:{ident(e.label)}) "
        f"RETURN s.{ident(s.key)} AS s, e.{ident(e.key)} AS e",
    )
    statement = (
        f"UNWIND $rows AS r MATCH (a:{ident(s.label)} {{{ident(s.key)}: r.s}}) "
        f"MATCH (b:{ident(e.label)} {{{ident(e.key)}: r.e}}) MERGE (a)-[:{ident(rel.type)}]->(b)"
    )
    created = batches = 0
    for i in range(0, len(pairs), max(1, batch)):
        data = pairs[i : i + max(1, batch)]
        summary = session.execute_write(lambda tx, d=data: tx.run(statement, rows=d).consume())
        created += summary.counters.relationships_created
        batches += 1
    return PushEntity(entity=rel.entity, kind="relationship", total=len(pairs), created=created,
                      updated=len(pairs) - created, batches=batches, landed=True)


def _bolt_labrun(session, run_id: str, props: dict[str, Any]) -> None:
    statement = f"MERGE (l:{ident(LABRUN_LABEL)} {{{ident(LABRUN_KEY)}: $id}}) SET l += $props"
    session.execute_write(lambda tx: tx.run(statement, id=run_id, props=props).consume())


__all__ = ["OWN_CONSTRAINTS", "PushError", "PushNotConfirmed", "apply", "ensure_master_constraints",
           "own_constraints_missing", "preview"]
