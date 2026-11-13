"""Track 3b and 5b: the evidence object, on the fixture graph (page 05, R16).

The fixture has 200 accounts, 2,000 payments, the mule ring R1 (six accounts on one device and address, a payment
cycle through the six), the structuring ring R2 (eight accounts on one email, each paying the collector just under
10,000) and the family L1 (four accounts on one laptop and address). `world` runs `score` once per module and keeps
what `attach_summaries` was given, so a test can call it again.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import polars as pl
import pytest

from lab.common import alert_key
from lab.contracts.evidence import Evidence
from lab.contracts.recipe import Recipe
from lab.runs import Run
from lab.scoring import alerts as make_alerts
from lab.scoring import run_factors, score
from lab.scoring.communities import COMMUNITIES_FILE
from lab.scoring.policy import account_scores, load_policy
from lab.settings import reload_settings
from lab.summaries import EvidenceContext, build_all, build_evidence, read_evidence, validate_evidence
from lab.summaries import evidence as evidence_module
from tests.fixtures.make_fixture import L1, R1, R2

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "lab" / "contracts" / "schemas" / "Evidence.json"


@pytest.fixture(autouse=True)
def forget_settings():
    """A test that sets a variable through monkeypatch leaves nothing behind: settings are read again afterwards."""
    yield
    reload_settings()


def community_of(world, members: list[str]) -> str:
    row = world.communities.filter(pl.col("member_keys").list.contains(members[0])).row(0, named=True)
    assert set(members) <= set(row["member_keys"])
    return row["community_key"]


@pytest.fixture(scope="module")
def world(tmp_path_factory, fixture_store_path):
    """A run on the fixture graph after `score`, plus the inputs `attach_summaries` takes."""
    mp = pytest.MonkeyPatch()
    data = tmp_path_factory.mktemp("summaries") / "data"
    data.mkdir()
    mp.setenv("LAB_DATA_DIR", str(data))
    mp.setenv("LAB_FACTORS_FILE", str(REPO / "factors" / "factors.yaml"))
    mp.setenv("LAB_POLICY_FILE", str(REPO / "factors" / "policy.yaml"))
    mp.delenv("ANTHROPIC_API_KEY", raising=False)
    mp.delenv("LAB_RUN_ID", raising=False)
    reload_settings()
    run = Run.create(Recipe(), data_dir=data)
    shutil.copytree(fixture_store_path, run.paths.store)
    counts = score(run)
    factor_run = run_factors(run, None)  # what attach_summaries is given, made again: the same by construction
    policy = load_policy(run)
    found = make_alerts(run, account_scores(run, factor_run, policy), policy, factor_run)
    world = SimpleNamespace(
        run=run, counts=counts, factor_run=factor_run, found=found,
        communities=pl.read_parquet(run.paths.artifacts / COMMUNITIES_FILE),
        ctx=EvidenceContext.load(run, factor_run, found),
    )
    yield world
    run.close()
    mp.undo()
    reload_settings()


def payments(run: Run) -> pl.DataFrame:
    return run.table("MATCH (s:Account)-[t:TX]->(d:Account) RETURN s.account_id AS src, d.account_id AS dst, "
                     "t.amount AS amount, t.ts AS ts")


# ---- the object --------------------------------------------------------------------------------------------------
def test_a_community_evidence_has_every_part_and_passes_its_schema(world):
    ev = build_evidence(world.run, "community", community_of(world, R1), world.ctx)
    jsonschema.validate(ev.model_dump(mode="json"), json.loads(SCHEMA.read_text(encoding="utf-8")))  # the exported file
    validate_evidence(ev)
    assert (ev.subject, ev.run_id, ev.member_count, ev.member_keys) == ("community", world.run.id, 6, sorted(R1))
    assert 0 < ev.score <= 1 and set(ev.score_parts) == {
        "member_risk", "shared_identifiers", "cycles", "new_accounts", "speed", "formed_by_identifier"}
    assert ev.score_parts["formed_by_identifier"] == 1.0  # the ring is a community of shared identifiers
    assert {(s.kind, s.key, s.members_sharing) for s in ev.shared_identifiers} == {("device", "D-R1", 6), ("address", "AD-R1", 6)}
    assert ev.money.cycles == 1  # A0011 to A0016 and back to A0011: one cycle, however often it was paid
    assert ev.money.first_at.endswith("Z") and ev.money.first_at < ev.money.last_at
    assert ev.money.span_hours == pytest.approx(342.0)
    fired = {f.factor_id: f for f in ev.factors}
    assert fired["shared_device_5plus"].accounts == 6 and fired["shared_device_5plus"].evidence_ids == ["D-R1"]
    assert fired["in_and_out_within_a_day"].evidence_ids and len(fired["in_and_out_within_a_day"].evidence_ids) <= 10
    assert fired["shared_device_5plus"].name == "Five or more accounts on one device"  # the name, not a label of ours
    assert len(ev.top_members) == 5 and [m.score for m in ev.top_members] == sorted((m.score for m in ev.top_members), reverse=True)
    assert {m.key for m in ev.top_members} <= set(R1)
    c = ev.comparison
    assert (c.communities_in_slice, c.score_rank) == (10, 1) and c.median_size > 0 and 0 <= c.median_score <= 1


def test_the_money_matches_the_payments_worked_out_by_hand(world):
    pay = payments(world.run)
    for members in (R1, R2, L1):
        ev = build_evidence(world.run, "community", community_of(world, members), world.ctx)
        mine = set(ev.member_keys)
        inside = pay.filter(pl.col("src").is_in(mine) & pl.col("dst").is_in(mine) & (pl.col("src") != pl.col("dst")))
        into = pay.filter(pl.col("dst").is_in(mine) & ~pl.col("src").is_in(mine))
        out = pay.filter(pl.col("src").is_in(mine) & ~pl.col("dst").is_in(mine))
        touching = pay.filter(pl.col("src").is_in(mine) | pl.col("dst").is_in(mine))
        assert ev.money.internal == pytest.approx(inside["amount"].sum(), abs=0.01)
        assert ev.money.inflow == pytest.approx(into["amount"].sum(), abs=0.01)
        assert ev.money.outflow == pytest.approx(out["amount"].sum(), abs=0.01)
        assert ev.money.first_at.startswith(str(touching["ts"].min())[:10])
        assert ev.money.last_at.startswith(str(touching["ts"].max())[:10])
    r2 = build_evidence(world.run, "community", community_of(world, R2), world.ctx)
    assert {f.factor_id for f in r2.factors} >= {"just_under_threshold", "new_accounts_shared_identifier"}
    assert [(s.kind, s.key, s.members_sharing) for s in r2.shared_identifiers] == [("email", "E-R2", 8)]
    assert r2.money.cycles == 0  # structuring pays one way
    l1 = build_evidence(world.run, "community", community_of(world, L1), world.ctx)
    assert l1.factors == [] and l1.money.cycles >= 1  # a family paying each other back and forth has short cycles too


def test_evidence_is_deterministic(world):
    key = community_of(world, R1)
    a = build_evidence(world.run, "community", key, world.ctx)
    b = build_evidence(world.run, "community", key, EvidenceContext.load(world.run, world.factor_run, world.found))
    assert a == b and a.hash() == b.hash() and len(a.hash()) == 64
    assert build_all(world.ctx).keys() == build_all(world.ctx).keys()
    assert all(build_all(world.ctx)[k].hash() == v.hash() for k, v in build_all(world.ctx).items())


def test_the_hash_changes_when_a_number_changes(world):
    ev = build_evidence(world.run, "community", community_of(world, R1), world.ctx)
    assert ev.model_copy().hash() == ev.hash()
    moved = ev.model_copy(update={"money": ev.money.model_copy(update={"inflow": ev.money.inflow + 0.01})})
    assert moved.hash() != ev.hash()
    assert ev.model_copy(update={"member_count": ev.member_count + 1}).hash() != ev.hash()
    assert ev.model_copy(update={"score": round(ev.score - 0.0001, 4)}).hash() != ev.hash()
    with pytest.raises(Exception, match="extra"):  # an object with a field the schema does not have is refused
        Evidence(**{**ev.model_dump(), "full_name": "Jane Doe"})


def test_evidence_carries_keys_and_totals_never_names(world):
    """Give every account a name in the run store; no object, and no file, may carry one."""
    world.run.store().execute("MATCH (a:Account) SET a.name = 'Jane Q Public', a.holder = 'Quentin Placeholder'")
    ctx = EvidenceContext.load(world.run, world.factor_run, world.found)
    objects = build_all(ctx)
    text = " ".join(e.model_dump_json() for e in objects.values())
    assert "Jane" not in text and "Quentin" not in text and "Public" not in text
    for e in objects.values():
        strings = _strings(e.model_dump(mode="json"))
        keys = {*e.member_keys, *(s.key for s in e.shared_identifiers), e.key, e.run_id, e.subject, e.money.first_at,
                e.money.last_at, *(m.key for m in e.top_members), *(f.factor_id for f in e.factors),
                *(f.name for f in e.factors), *(i for f in e.factors for i in f.evidence_ids),
                *(s.kind for s in e.shared_identifiers)}
        assert set(strings) <= keys, set(strings) - keys


def _strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


# ---- an alert ----------------------------------------------------------------------------------------------------
def test_an_alert_has_the_same_shape_with_one_member(world):
    row = world.found.filter(pl.col("account_key") == R1[0]).row(0, named=True)
    ev = build_evidence(world.run, "alert", row["alert_key"], world.ctx)
    validate_evidence(ev)
    assert (ev.subject, ev.key, ev.member_count, ev.member_keys) == ("alert", row["alert_key"], 1, [R1[0]])
    assert ev.score == round(row["score"], 4) and set(ev.score_parts) == {"rules", "model"}
    assert (ev.money.internal, ev.money.cycles) == (0.0, 0)
    assert ev.money.inflow > 0 and ev.money.outflow > 0
    assert {(s.kind, s.key, s.members_sharing) for s in ev.shared_identifiers} == {("device", "D-R1", 6), ("address", "AD-R1", 6)}
    assert all(f.accounts == 1 for f in ev.factors) and {"shared_device_5plus"} <= {f.factor_id for f in ev.factors}
    assert [(m.key, m.score) for m in ev.top_members] == [(R1[0], ev.score)]
    assert ev.comparison.communities_in_slice == 10 and ev.comparison.score_rank >= 1


def test_the_alert_key_is_the_documented_hash_and_every_alert_has_evidence(world):
    built = build_all(world.ctx)
    window = load_policy(world.run).window
    assert all(alert_key(world.run.id, a, window) in built for a in world.found["account_key"])
    assert sum(e.subject == "alert" for e in built.values()) == world.found.height
    assert sum(e.subject == "community" for e in built.values()) == 10


def test_evidence_for_every_alert_and_community_is_saved_where_explain_alert_reads_it(world):
    built = build_all(world.ctx)
    folder = world.run.paths.artifacts / "evidence"
    assert {p.stem for p in folder.glob("*.json")} == set(built)
    for key, ev in built.items():
        assert read_evidence(world.run, key) == ev
    assert read_evidence(world.run, "no-such-key") is None


def test_a_context_read_back_from_the_artifacts_gives_the_same_objects(world):
    """No factor run in hand (explain_alert, a later process): the factor results and communities are read from files."""
    again = EvidenceContext.load(world.run)
    key = community_of(world, R2)
    assert build_evidence(world.run, "community", key, again).hash() == read_evidence(world.run, key).hash()
    alert = world.found["alert_key"][0]
    assert build_evidence(world.run, "alert", alert, again).hash() == read_evidence(world.run, alert).hash()


def test_an_unknown_key_or_subject_is_an_error(world):
    with pytest.raises(KeyError, match="no community"):
        build_evidence(world.run, "community", "nope", world.ctx)
    with pytest.raises(ValueError, match="subject"):
        build_evidence(world.run, "account", R1[0], world.ctx)


def test_an_identifier_used_by_too_many_accounts_is_left_out(world, monkeypatch):
    monkeypatch.setenv("LAB_SHARED_MAX", "5")  # the community score leaves out an identifier this many accounts use
    reload_settings()
    shared = lambda members: build_evidence(world.run, "community", community_of(world, members), world.ctx).shared_identifiers  # noqa: E731
    assert shared(R1) == [] and shared(R2) == []  # six and eight accounts use them
    monkeypatch.setenv("LAB_SHARED_MAX", "7")
    reload_settings()
    assert {s.key for s in shared(R1)} == {"D-R1", "AD-R1"} and shared(R2) == []
    monkeypatch.setenv("LAB_SHARED_MAX", "8")
    reload_settings()
    assert [s.key for s in shared(R2)] == ["E-R2"]


# ---- money on small graphs ---------------------------------------------------------------------------------------
def pay_frame(rows: list[tuple[str, str, float, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"src": [r[0] for r in rows], "dst": [r[1] for r in rows], "amount": [r[2] for r in rows],
         "ts": [r[3] for r in rows]},
        schema={"src": pl.Utf8, "dst": pl.Utf8, "amount": pl.Float64, "ts": pl.Utf8},
    ).with_columns(pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%S", time_unit="us"))


def members_of(*groups: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {"account": [m for g in groups for m in g], "g": [i for i, g in enumerate(groups) for _ in g]},
        schema={"account": pl.Utf8, "g": pl.Int64},
    )


def test_money_in_out_between_and_cycles_on_a_small_graph():
    pay = pay_frame([
        ("x", "a", 100.0, "2026-10-01T10:00:00"),  # in from outside
        ("a", "b", 60.0, "2026-10-01T11:00:00"),
        ("b", "c", 55.0, "2026-10-01T12:00:00"),
        ("c", "a", 50.0, "2026-10-02T10:00:00"),  # a, b, c: one cycle
        ("c", "y", 40.0, "2026-10-03T10:00:00"),  # out to outside
        ("a", "a", 999.0, "2026-10-04T10:00:00"),  # to itself: not money moving
        ("p", "q", 5.0, "2026-10-05T10:00:00"),  # touches nobody
    ])
    got = evidence_module._money(pay, members_of(["a", "b", "c"]))[0]
    assert (got.inflow, got.outflow, got.internal, got.cycles) == (100.0, 40.0, 165.0, 1)
    assert (got.first_at, got.last_at, got.span_hours) == ("2026-10-01T10:00:00Z", "2026-10-03T10:00:00Z", 48.0)


def test_each_group_is_counted_apart_and_a_payment_between_groups_is_out_for_one_and_in_for_the_other():
    pay = pay_frame([("a", "b", 10.0, "2026-10-01T10:00:00"), ("b", "a", 4.0, "2026-10-02T10:00:00")])
    first, second = evidence_module._money(pay, members_of(["a"], ["b"])).values()
    assert (first.inflow, first.outflow, first.internal, first.cycles) == (4.0, 10.0, 0.0, 0)
    assert (second.inflow, second.outflow, second.internal, second.cycles) == (10.0, 4.0, 0.0, 0)
    nothing = evidence_module._money(pay_frame([]), members_of(["a", "b"]))[0]
    assert (nothing.inflow, nothing.first_at, nothing.span_hours, nothing.cycles) == (0.0, None, None, 0)


def test_cycles_are_counted_up_to_a_cap(monkeypatch):
    names = [f"n{i}" for i in range(6)]
    # everyone pays everyone, each payment a minute after the one before it, so there is a lot of money going round
    rows = [(a, b, 1.0, f"2026-10-01T10:{i // 60:02d}:{i % 60:02d}")
            for i, (a, b) in enumerate((a, b) for a in names for b in names if a != b)]
    full = evidence_module._money(pay_frame(rows), members_of(names))[0].cycles
    assert full > 20
    monkeypatch.setenv("LAB_CYCLE_COUNT_CAP", "7")  # a setting, read when the evidence is built
    reload_settings()
    assert evidence_module._money(pay_frame(rows), members_of(names))[0].cycles == 7


def test_a_ring_paid_in_the_wrong_order_has_no_cycle_in_the_evidence():
    forward = pay_frame([("a", "b", 5.0, "2026-10-01T10:00:00"), ("b", "c", 5.0, "2026-10-02T10:00:00"),
                         ("c", "a", 5.0, "2026-10-03T10:00:00")])
    backward = pay_frame([("a", "b", 5.0, "2026-10-03T10:00:00"), ("b", "c", 5.0, "2026-10-02T10:00:00"),
                          ("c", "a", 5.0, "2026-10-01T10:00:00")])
    assert evidence_module._money(forward, members_of(["a", "b", "c"]))[0].cycles == 1
    got = evidence_module._money(backward, members_of(["a", "b", "c"]))[0]
    assert got.cycles == 0 and got.internal == 15.0  # the same money among the same three, in the order that is no loop


def test_the_evidence_and_the_community_score_count_cycles_the_same_way(world):
    """One definition: the cycles in the evidence are the ones `signals.payment_cycles` finds among the members' payments,
    and the community's `cycles` part is positive exactly when there are some."""
    for members in (R1, R2, L1):
        ev = build_evidence(world.run, "community", community_of(world, members), world.ctx)
        assert (ev.money.cycles > 0) == (ev.score_parts["cycles"] > 0)


def test_how_much_an_object_lists_is_a_setting(world, monkeypatch):
    key = community_of(world, R1)
    monkeypatch.setenv("LAB_EVIDENCE_TOP_MEMBERS", "2")
    monkeypatch.setenv("LAB_EVIDENCE_MAX_IDENTIFIERS", "1")
    monkeypatch.setenv("LAB_EVIDENCE_MAX_IDS", "1")
    reload_settings()
    ev = build_evidence(world.run, "community", key, EvidenceContext.load(world.run, world.factor_run, world.found))
    assert len(ev.top_members) == 2 and len(ev.shared_identifiers) == 1
    assert ev.factors and all(len(f.evidence_ids) <= 1 for f in ev.factors)
    assert any(len(f.evidence_ids) == 1 for f in ev.factors)
