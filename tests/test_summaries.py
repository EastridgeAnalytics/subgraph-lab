"""Track 3b and 5b: summaries (page 05, R16 to R18, A9): the template, the checks, the three writers, the cache, the step.

The Anthropic client is always a fake: no key is set on this machine, no test calls the API, and a test that sets a key
makes `anthropic.Anthropic` return a fake whose calls it records. `world` (from `test_evidence`) is a run after `score`
on the fixture graph; tests that need a clean run use `fixture_run` and `score`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import polars as pl
import pytest

from lab.common import canonical_json
from lab.common.store import rows
from lab.contracts.evidence import Comparison, Evidence, FactorHit, MemberScore, Money, SharedIdentifier
from lab.contracts.graph import ALERT_PROPS, COMMUNITY_PROPS
from lab.contracts.manifest import StepStatus
from lab.contracts.recipe import Recipe
from lab.roundtrip.indexes import create_indexes
from lab.runs import Run
from lab.scoring import score
from lab.scoring.pipeline import attach_summaries
from lab.settings import reload_settings
from lab.summaries import (
    VERDICT_WORDS,
    SummaryCache,
    Writer,
    build_evidence,
    check,
    describe,
    read_evidence,
    read_summary,
    summarise,
    template_summary,
)
from lab.summaries.cache import evidence_hash as cache_hash
from lab.summaries.checks import CHECKS, count_words, sentences
from lab.summaries.writers import MAX_API_ERRORS, SUMMARY_SCHEMA, SYSTEM_PROMPT
from tests import test_evidence
from tests.fixtures.make_fixture import L1, R1, R2
from tests.test_evidence import community_of

world = test_evidence.world  # the module's fixtures are shared with test_evidence: one run after score per module
forget_settings = test_evidence.forget_settings

MODEL = "claude-sonnet-5-5"
REPO = Path(__file__).resolve().parent.parent


# ---- helpers ------------------------------------------------------------------------------------------------------
def community_evidence(world, members) -> Evidence:
    return build_evidence(world.run, "community", community_of(world, members), world.ctx)


def alert_evidence(world, account: str = R1[0]) -> Evidence:
    key = world.found.filter(pl.col("account_key") == account)["alert_key"][0]
    return build_evidence(world.run, "alert", key, world.ctx)


def lookup(world):
    """`is_account` as the scoring step builds it: the accounts of the slice."""
    accounts = set(world.ctx.scores["key"].to_list())
    return lambda tokens: accounts.intersection(tokens)


def passes(text: str, ev: Evidence, **kw) -> bool:
    return check(text, ev, **kw)[1] is None


def tiny() -> Evidence:
    """Evidence made by hand, so every number in it is known."""
    return Evidence(
        subject="community", key="k1", run_id="run-1", member_count=3, member_keys=["A0001", "A0002", "A0003"],
        score=0.6234, score_parts={"cycles": 0.5},
        shared_identifiers=[SharedIdentifier(kind="device", key="D-1", members_sharing=3)],
        money=Money(inflow=4215.3, outflow=1200.0, internal=77.25, cycles=2, first_at="2026-10-01T09:10:00Z",
                    last_at="2026-10-04T10:00:00Z", span_hours=72.83),
        factors=[FactorHit(factor_id="just_under_threshold", name="Payments just under 10,000", accounts=3,
                           evidence_ids=["T1", "T2"])],
        top_members=[MemberScore(key="A0001", score=0.7)],
        comparison=Comparison(communities_in_slice=9, score_rank=2, median_size=4.0, median_score=0.31),
    )


def response(parts: dict | None = None, *, stop: str = "end_turn", text: str | None = None, blocks=None):
    body = text if text is not None else json.dumps(parts)
    return SimpleNamespace(stop_reason=stop, content=blocks if blocks is not None else [SimpleNamespace(type="text", text=body)])


def ideal(kw) -> SimpleNamespace:
    """What a model that follows the rules would answer: the template's three sentences, as the three parts."""
    ev = Evidence.model_validate_json(kw["messages"][0]["content"].split("Evidence:\n", 1)[1])
    shows, why, innocent = sentences(template_summary(ev))
    return response({"shows": shows, "why_risk": why, "innocent_explanation": innocent})


def with_planted_number(kw) -> SimpleNamespace:
    ev = Evidence.model_validate_json(kw["messages"][0]["content"].split("Evidence:\n", 1)[1])
    shows, why, innocent = sentences(template_summary(ev))
    return response({"shows": shows + " Another 4,321,987.65 moved through them.", "why_risk": why, "innocent_explanation": innocent})


class Api:
    """A fake `anthropic.Anthropic`: every constructor call and every `messages.create` call is recorded."""

    def __init__(self, handler):
        self.handler, self.calls, self.built = handler, [], []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        out = self.handler(kw)
        if isinstance(out, Exception):
            raise out
        return out

    def factory(self, **kw):
        self.built.append(kw)
        return self


def install(monkeypatch, handler, key: str = "sk-test", model: str | None = None) -> Api:
    """Set a key and make `anthropic.Anthropic` a fake. Settings are read again, so the key is seen."""
    api = Api(handler)
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    if model:
        monkeypatch.setenv("LAB_MODEL", model)
    reload_settings()
    monkeypatch.setattr(anthropic, "Anthropic", api.factory)
    return api


def forbid_client(monkeypatch) -> list[dict]:
    built: list[dict] = []

    def refuse(**kw):
        built.append(kw)
        raise AssertionError("an Anthropic client was built with no key set")

    monkeypatch.setattr(anthropic, "Anthropic", refuse)
    return built


REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# ---- the template -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("name", "members", "innocent"), [
    ("R1", R1, "a household or a shared computer"),
    ("R2", R2, "rent or an instalment plan"),
    ("L1", L1, "a household or a shared computer"),
])
def test_the_template_passes_its_own_checks_for_each_planted_group(world, name, members, innocent):
    ev = community_evidence(world, members)
    text = template_summary(ev)
    checks, why = check(text, ev, is_account=lookup(world))
    assert why is None and all(checks.values()), why
    assert count_words(text) <= 120 and len(sentences(text)) == 3
    assert "could indicate" in text and "innocent explanation to rule out" in text and innocent in text
    print(f"\ntemplate summary, {name}:\n{text}")


def test_the_template_for_an_alert_passes_its_checks_and_names_its_account(world):
    ev = alert_evidence(world)
    text = template_summary(ev)
    assert passes(text, ev, is_account=lookup(world)) and f"Account {R1[0]}" in text
    assert "ranking" in text and "among the slice's alerts" in text and "device D-R1 (used by 6 accounts)" in text
    print(f"\ntemplate summary, an R1 alert:\n{text}")


def test_the_template_is_the_same_text_every_time(world):
    ev = community_evidence(world, R1)
    assert template_summary(ev) == template_summary(ev.model_copy(deep=True))


def test_a_short_limit_drops_detail_and_keeps_the_three_parts(world):
    ev = community_evidence(world, R1)
    full = template_summary(ev)
    short = template_summary(ev, max_words=70)
    assert count_words(short) <= 70 < count_words(full)
    assert passes(short, ev, max_words=70, is_account=lookup(world))
    bare = template_summary(ev, max_words=5)  # nothing that small holds three sentences: they come anyway
    assert len(sentences(bare)) == 3 and passes(bare, ev, max_words=count_words(bare))


def with_(ev: Evidence, **update) -> Evidence:
    return ev.model_copy(update=update)


def test_the_innocent_explanation_follows_what_the_evidence_shows():
    base = with_(tiny(), shared_identifiers=[], factors=[], money=tiny().money.model_copy(update={"internal": 0.0, "cycles": 0}))
    says = lambda ev: template_summary(ev).split("An innocent explanation to rule out first is ")[1]  # noqa: E731
    assert says(base).startswith("ordinary activity")
    moving = base.money.model_copy(update={"internal": 5.0})
    assert says(with_(base, money=moving)).startswith("relatives or housemates")
    assert says(with_(base, money=moving, member_count=12)).startswith("a club, a team or a marketplace")
    kinds = {"device": "household or a shared computer", "address": "share a home or an office",
             "email": "family or business mailbox", "phone": "shared phone line", "bank": "shared resource"}
    for kind, words in kinds.items():
        ev = with_(base, shared_identifiers=[SharedIdentifier(kind=kind, key="X-1", members_sharing=3)])
        assert words in says(ev) and passes(template_summary(ev), ev), kind
    under = with_(base, factors=[FactorHit(factor_id="just_under_threshold", name="Payments just under 10,000", accounts=3)])
    assert says(under).startswith("rent or an instalment plan")
    for fid, name in (("fan_in_new_accounts", "Fan-in from new accounts"), ("in_and_out_within_a_day", "Money in and out")):
        ev = with_(base, factors=[FactorHit(factor_id=fid, name=name, accounts=2)])
        assert says(ev).startswith("a payroll or collections account") and passes(template_summary(ev), ev), fid
    both = with_(base, factors=under.factors, shared_identifiers=[SharedIdentifier(kind="device", key="D-1", members_sharing=3)])
    assert says(both) == "rent or an instalment plan paid in fixed amounts just below a round number, or a household or a shared computer."


def test_a_community_at_the_cycle_cap_is_said_to_have_that_many_or_more():
    ev = with_(tiny(), money=tiny().money.model_copy(update={"cycles": 1000}))
    text = template_summary(ev)
    assert "1,000 or more payment cycles" in text and passes(text, ev)
    one = with_(tiny(), money=tiny().money.model_copy(update={"cycles": 1}))
    assert "1 payment cycle among them" in template_summary(one)


def test_a_factor_named_with_a_verdict_word_is_shown_by_its_id():
    ev = with_(tiny(), factors=[FactorHit(factor_id="ring_check", name="Looks like a fraud ring", accounts=3)])
    text = template_summary(ev)
    assert "ring_check" in text and "fraud ring" not in text and passes(text, ev)


# ---- the checks ---------------------------------------------------------------------------------------------------
def test_a_check_gives_every_check_and_one_reason(world):
    ev = community_evidence(world, R1)
    checks, why = check(template_summary(ev), ev)
    assert tuple(checks) == CHECKS and all(checks.values()) and why is None
    checks, why = check("It moved 77777 in total.", ev)
    assert tuple(checks) == CHECKS and not checks["numbers"] and not checks["parts"] and checks["verdict_words"]
    assert "numbers: 77777 is not in the evidence" in why and "parts:" in why and "length" not in why


def test_a_number_that_is_not_in_the_evidence_fails(world):
    ev = community_evidence(world, R1)
    text = template_summary(ev) + " It also moved 4,321,987.65 and 77777 accounts."
    checks, why = check(text, ev)
    assert checks["numbers"] is False and all(v for k, v in checks.items() if k != "numbers")
    assert "4,321,987.65 is not in the evidence" in why and "77777 is not in the evidence" in why


@pytest.mark.parametrize("fragment", [
    "3 accounts", "three accounts", "4,215.30", "4215.3", "4,215", "4215.30", "1,200", "1200.00", "77.25", "77",
    "0.62", "0.6", "62%", "62.3%", "2 cycles", "ranking 2nd of 9", "a median of 4", "72.83 hours", "73 hours",
    "under 10,000", "2026-10-01", "2026-10-01T09:10:00Z", "2026-10-04", "1 shared identifier", "2 evidence ids",
    "between A0001 and A0002", "on device D-1",
])
def test_these_numbers_appear_in_the_evidence(fragment):
    assert check(f"{fragment}.", tiny())[0]["numbers"] is True, fragment


@pytest.mark.parametrize("fragment", [
    "5 accounts", "five accounts", "4,215.31", "4,216", "4,200", "0.9", "63%", "100%", "62", "73.5", "2026", "2026-10-02",
    "11 October", "seven", "twenty", "a hundred", "a dozen accounts", "13 cycles", "0.0624", "10 hours",
])
def test_these_numbers_do_not(fragment):
    checks, why = check(f"{fragment}.", tiny())
    assert checks["numbers"] is False, fragment
    assert "is not in the evidence" in why


def test_an_account_outside_the_community_fails(world):
    ev = community_evidence(world, R1)
    good = template_summary(ev)
    real = "A0001"  # an account of the slice that is not in R1
    assert real not in ev.member_keys
    for is_account in (lookup(world), None):  # the lookup finds it; the shape of the members' keys finds it too
        checks, why = check(good + f" Account {real} is also involved.", ev, is_account=is_account)
        assert checks["members"] is False and f"{real} is not a member" in why
    invented = check(good + " Account A9999 is also involved.", ev)  # no such account; it looks like one
    assert invented[0]["members"] is False and "A9999 is not a member" in invented[1]
    assert check(good + " Account A9999.", ev)[0]["numbers"] is False  # and its digits are not in the evidence either
    other = check(good + " See acct-x.", ev, is_account=lambda tokens: {"acct-x"}.intersection(tokens))
    assert other[0]["members"] is False  # the lookup works for any shape of key
    assert passes(good + f" {R1[0]} and {R1[1]} lead.", ev, is_account=lookup(world))  # members are fine
    assert "D-R1" in good and passes(good, ev, is_account=lookup(world))  # and so is a shared identifier's key
    assert check(good + " Payment T00005.", ev)[0]["members"] is False  # a payment id is not named either


def test_an_account_the_text_names_must_be_a_member_even_when_the_digits_are_in_the_evidence():
    ev = tiny()
    text = "A0001 and A0002 share device D-1. That could indicate shared use. A benign cause is a household. A0004 too."
    checks, why = check(text, ev)
    assert checks["members"] is False and "A0004 is not a member" in why
    assert check(text.replace("A0004", "A0003"), ev)[0]["members"] is True


GOOD_TAIL = " That could indicate coordination. An innocent explanation to rule out is a household."


@pytest.mark.parametrize("word", VERDICT_WORDS)
def test_each_verdict_word_fails(word):
    ev = tiny()
    for form in (word, word.upper(), word + "s"):
        checks, why = check(f"Three accounts share device D-1 and look like a {form}.{GOOD_TAIL}", ev)
        assert checks["verdict_words"] is False, form
        assert "is a verdict word" in why and checks["numbers"] and checks["members"] and checks["parts"]


def test_the_verdict_list_has_the_ten_the_brief_names_and_stays_in_one_place():
    ten = {"fraudster", "fraud ring", "criminal", "guilty", "laundering", "launderer", "scam", "scammer", "stole", "theft"}
    assert ten <= set(VERDICT_WORDS)
    ev = tiny()
    for fine in ("could indicate fraud", "a money mule pattern", "scampi", "laundry", "a thieving magpie"):
        assert check(f"Three accounts share device D-1 and {fine}.{GOOD_TAIL}", ev)[0]["verdict_words"] is True, fine


def test_too_many_words_fails(world, monkeypatch):
    ev = tiny()
    text = "Three accounts share device D-1." + GOOD_TAIL
    assert check(text, ev)[0]["length"] is True
    long = text + " filler" * 120
    checks, why = check(long, ev)
    assert checks["length"] is False and f"{count_words(long)} words; the limit is 120" in why
    assert check(text, ev, max_words=count_words(text))[0]["length"] is True
    assert check(text, ev, max_words=count_words(text) - 1)[0]["length"] is False
    monkeypatch.setenv("LAB_SUMMARY_WORDS", "10")  # the default limit is the setting
    reload_settings()
    assert check(text, ev)[0]["length"] is False


@pytest.mark.parametrize(("text", "ok", "why"), [
    ("Three accounts share device D-1. That could indicate coordination. An innocent explanation to rule out is a household.", True, ""),
    ("Three accounts share device D-1. That may suggest coordination. A legitimate cause is a household.", True, ""),
    ("Three accounts share device D-1. That might point to coordination. This is explained by a household.", True, ""),
    ("Three accounts share device D-1. A household could explain it.", False, "2 sentences"),
    ("Three accounts share device D-1. That is odd. An innocent explanation to rule out is a household.", False, "could indicate"),
    ("That could indicate coordination. Three accounts share device D-1. An innocent explanation to rule out is a household.", False, "comes first"),
    ("Three accounts share device D-1. That could indicate coordination. Nothing else is known.", False, "innocent explanation"),
    ("Three accounts share device D-1. That could indicate a legitimate purpose. Nothing else is known.", False, "innocent explanation"),
    ("", False, "0 sentences"),
])
def test_the_three_parts_are_told_by_their_sentences(text, ok, why):
    checks, reason = check(text, tiny())
    assert checks["parts"] is ok
    assert why in (reason or "")


# ---- the API writer -----------------------------------------------------------------------------------------------
def test_the_api_call_has_the_documented_shape_and_a_good_answer_is_stored_as_api(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    api = install(monkeypatch, ideal)
    written = Writer(cache=SummaryCache(tmp_path / "c.jsonl"), is_account=lookup(world)).write(ev)

    assert api.built == [{"api_key": "sk-test"}]
    (kw,) = api.calls
    assert set(kw) == {"model", "max_tokens", "system", "messages", "output_config"}  # no sampling parameters, no thinking
    assert kw["model"] == MODEL == reload_settings().model and kw["max_tokens"] == 4000 and kw["system"] == SYSTEM_PROMPT
    assert [m["role"] for m in kw["messages"]] == ["user"]  # no prefill
    content = kw["messages"][0]["content"]
    assert canonical_json(ev.model_dump(mode="json")) in content and "At most 120 words" in content
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}}
    schema = kw["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["shows", "why_risk", "innocent_explanation"]
    assert {p["type"] for p in schema["properties"].values()} == {"string"} and set(schema["properties"]) == set(schema["required"])
    banned = {"temperature", "top_p", "top_k", "thinking", "tool_choice", "stop_sequences"}
    assert not banned & set(kw) and "budget_tokens" not in json.dumps(kw, default=str)
    for rule in ("pattern", "could indicate", "innocent explanation", "number", "member_keys"):
        assert rule in SYSTEM_PROMPT

    s = written.summary
    assert (s.source, s.model, s.evidence_hash, s.failed_reason) == ("api", MODEL, ev.hash(), None)
    assert s.text == template_summary(ev)  # the fake answers with the template's sentences, joined
    assert all(s.checks.values()) and written.cached is False and written.failed_check is False


def test_a9_an_answer_with_a_planted_number_is_replaced_by_the_template(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    install(monkeypatch, with_planted_number)
    cache = SummaryCache(tmp_path / "c.jsonl")
    written = Writer(cache=cache, is_account=lookup(world)).write(ev)
    s = written.summary
    assert s.source == "template" and s.model is None and s.text == template_summary(ev)
    assert "4,321,987.65 is not in the evidence" in s.failed_reason and s.failed_reason.startswith("checks failed: numbers")
    assert s.checks["numbers"] is False and written.failed_check is True
    assert len(cache) == 0 and not (tmp_path / "c.jsonl").exists()  # a failed answer is not cached


def api_error(status: int) -> Exception:
    return anthropic.InternalServerError("boom", response=httpx.Response(status, request=REQUEST), body=None)


@pytest.mark.parametrize(("handler", "reason"), [
    (lambda kw: response({}, stop="refusal"), "the model refused"),
    (lambda kw: response({"shows": "x"}, stop="max_tokens"), "stopped at max_tokens"),
    (lambda kw: response({}, stop="model_context_window_exceeded"), "context window"),
    (lambda kw: anthropic.RateLimitError("slow down", response=httpx.Response(429, request=REQUEST), body=None), "rate limited (429)"),
    (lambda kw: anthropic.APIConnectionError(request=REQUEST), "connection error"),
    (lambda kw: anthropic.APITimeoutError(request=REQUEST), "connection error"),
    (lambda kw: api_error(500), "status 500"),
    (lambda kw: anthropic.AuthenticationError("no", response=httpx.Response(401, request=REQUEST), body=None), "status 401"),
    (lambda kw: response(text="not json at all"), "not JSON"),
    (lambda kw: response({"shows": "x", "why_risk": "y"}), "three parts"),
    (lambda kw: response({"shows": "x", "why_risk": "y", "innocent_explanation": ""}), "three parts"),
    (lambda kw: response(text="[1, 2, 3]"), "three parts"),
    (lambda kw: response(blocks=[SimpleNamespace(type="thinking", thinking="hm")]), "no text block"),
])
def test_a_refusal_a_cut_answer_an_api_error_or_a_bad_answer_gives_the_template_with_a_reason(
    world, monkeypatch, tmp_path, handler, reason
):
    ev = community_evidence(world, R1)
    install(monkeypatch, handler)
    cache = SummaryCache(tmp_path / "c.jsonl")
    written = Writer(cache=cache, is_account=lookup(world)).write(ev)
    s = written.summary
    assert s.source == "template" and s.model is None and s.text == template_summary(ev)
    assert reason in s.failed_reason and s.failed_reason.startswith("api: ")
    assert written.failed_check is False and written.cached is False and len(cache) == 0


def test_after_three_errors_in_a_row_the_api_is_left_alone(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    answers = iter([api_error(500)] * MAX_API_ERRORS + [ideal])
    api = install(monkeypatch, lambda kw: (lambda a: a(kw) if callable(a) else a)(next(answers)))
    writer = Writer(cache=SummaryCache(tmp_path / "c.jsonl"), is_account=lookup(world))
    for _ in range(MAX_API_ERRORS):
        assert "status 500" in writer.write(ev).summary.failed_reason
    held = writer.write(ev).summary
    assert len(api.calls) == MAX_API_ERRORS and held.source == "template" and "not called" in held.failed_reason
    assert len(api.built) == 1  # one client, built when first needed


def test_an_error_between_good_answers_does_not_stop_the_api(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    variants = [ev.model_copy(update={"score": round(0.1 + i / 100, 2)}) for i in range(9)]  # nine hashes: no cache hits
    api = install(monkeypatch, lambda kw: api_error(500) if len(api.calls) % 2 else ideal(kw))
    writer = Writer(cache=SummaryCache(tmp_path / "c.jsonl"), is_account=lookup(world))
    sources = [writer.write(e).summary.source for e in variants]
    assert sources == ["template", "api"] * 4 + ["template"] and len(api.calls) == 9  # never three failures in a row


# ---- the cache ----------------------------------------------------------------------------------------------------
def test_the_cache_prevents_a_second_call(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    api = install(monkeypatch, ideal)
    path = tmp_path / "cache" / "summaries.jsonl"
    first = Writer(cache=SummaryCache(path), is_account=lookup(world)).write(ev)
    again = Writer(cache=SummaryCache(path), is_account=lookup(world)).write(ev)  # a new writer, a new process in effect
    assert len(api.calls) == 1 and len(api.built) == 1
    assert (first.cached, again.cached) == (False, True)
    assert again.summary.source == "api" and again.summary.model == MODEL and again.summary.text == first.summary.text
    (line,) = path.read_text(encoding="utf-8").splitlines()
    assert {k: json.loads(line)[k] for k in ("evidence_hash", "model")} == {"evidence_hash": cache_hash(ev), "model": MODEL}


def test_the_cache_key_is_the_evidence_hash_plus_the_model(world, monkeypatch, tmp_path):
    ev, other = community_evidence(world, R1), community_evidence(world, R2)
    path = tmp_path / "c.jsonl"
    api = install(monkeypatch, ideal)
    Writer(cache=SummaryCache(path), is_account=lookup(world)).write(ev)
    Writer(cache=SummaryCache(path), is_account=lookup(world)).write(other)  # other evidence: a call
    assert len(api.calls) == 2
    install(monkeypatch, ideal, model="claude-other-1")
    again = Writer(cache=SummaryCache(path), is_account=lookup(world)).write(ev)  # the same evidence, another model: a call
    assert again.cached is False and again.summary.model == "claude-other-1"
    assert len(SummaryCache(path)) == 3
    changed = ev.model_copy(update={"score": 0.1234})  # other numbers, so another hash
    assert SummaryCache(path).get(cache_hash(changed), MODEL) is None


def test_the_template_is_never_cached_as_an_api_answer(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    path = tmp_path / "c.jsonl"
    forbid_client(monkeypatch)
    Writer(cache=SummaryCache(path), is_account=lookup(world)).write(ev)  # no key: the template
    assert not path.exists()
    install(monkeypatch, lambda kw: api_error(500))
    Writer(cache=SummaryCache(path), is_account=lookup(world)).write(ev)  # a failed call: the template
    assert not path.exists()
    cache = SummaryCache(path)
    cache.put(ev.hash(), MODEL, "x")
    cache.put(ev.hash(), MODEL, "x")  # the same answer twice is one line
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_a_cached_answer_is_checked_again_and_a_bad_one_is_asked_for_again(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    path = tmp_path / "c.jsonl"
    SummaryCache(path).put(cache_hash(ev), MODEL, "Three accounts are a fraudster. That could indicate coordination. A benign cause is a household.")
    path.write_text("not json\n" + path.read_text(encoding="utf-8") + '{"evidence_hash": "x"}\n', encoding="utf-8")  # damaged lines
    api = install(monkeypatch, ideal)
    written = Writer(cache=SummaryCache(path), is_account=lookup(world)).write(ev)
    assert len(api.calls) == 1 and written.cached is False and "fraudster" not in written.summary.text
    assert SummaryCache(path).get(cache_hash(ev), MODEL) == written.summary.text  # the new answer wins


def test_the_cache_hash_leaves_out_the_run_and_the_key_and_nothing_else(world):
    ev = community_evidence(world, R1)
    other_run = ev.model_copy(update={"run_id": "run-another", "key": "another-key"})
    assert other_run.hash() != ev.hash() and cache_hash(other_run) == cache_hash(ev)  # the object's name is not the pattern
    assert len(cache_hash(ev)) == 64
    for change in ({"score": 0.1234}, {"member_count": ev.member_count + 1}, {"member_keys": ev.member_keys[::-1][:-1]},
                   {"money": ev.money.model_copy(update={"inflow": ev.money.inflow + 0.01})}):
        assert cache_hash(ev.model_copy(update=change)) != cache_hash(ev)


def test_the_same_evidence_in_a_new_run_hits_the_cache(data_dir, fixture_store_path, monkeypatch):
    """Two runs of the same data: the second finds every answer the first paid for, though its run id and every
    community and alert key are new."""
    api = install(monkeypatch, ideal)
    first, second = Run.create(Recipe(), data_dir=data_dir), Run.create(Recipe(), data_dir=data_dir)
    try:
        for run in (first, second):
            shutil.copytree(fixture_store_path, run.paths.store)
        a = score(first)
        calls = len(api.calls)
        assert (a["from_api"], a["cached"]) == (a["summaries"], 0) and calls == a["summaries"] > 0
        b = score(second)
        assert first.id != second.id and (b["from_api"], b["cached"]) == (b["summaries"], b["summaries"])
        assert len(api.calls) == calls  # no call for the second run
        keys = lambda run: {r["k"] for r in rows(run.push_store(), "MATCH (n) WHERE n.summary_source IS NOT NULL RETURN coalesce(n.community_key, n.alert_key) AS k")}  # noqa: E731
        assert keys(first).isdisjoint(keys(second)) and len(keys(first)) == len(keys(second)) == b["summaries"]
    finally:
        first.close()
        second.close()


# ---- nothing leaves the container without a key -------------------------------------------------------------------
def test_with_no_key_no_client_is_ever_built(world, monkeypatch, tmp_path):
    built = forbid_client(monkeypatch)
    ev = community_evidence(world, R1)
    for key in (None, ""):
        if key is not None:
            monkeypatch.setenv("ANTHROPIC_API_KEY", key)
            reload_settings()
        written = Writer(cache=SummaryCache(tmp_path / "c.jsonl"), is_account=lookup(world)).write(ev)
        assert written.summary.source == "template" and written.summary.failed_reason is None
        assert written.summary.model is None and written.cached is False
    monkeypatch.setenv("LAB_DATA_DIR", str(tmp_path))
    reload_settings()
    found, _, counts = attach_summaries(world.run, world.found, world.factor_run)  # the whole step, no key
    assert built == [] and counts["from_api"] == 0 and counts["from_template"] == counts["summaries"] > 0
    assert not (tmp_path / "cache").exists()
    note = Run(world.run.id, world.run.paths.root.parent.parent).manifest.step("summaries").note
    assert "no ANTHROPIC_API_KEY" in note


# ---- the step -----------------------------------------------------------------------------------------------------
def test_attach_summaries_stages_summaries_on_alerts_and_communities_and_indexes_them(fixture_run):
    counts = score(fixture_run)
    run = fixture_run
    assert {k: counts[k] for k in ("summaries", "from_api", "from_template", "failed_checks", "cached")} == {
        "summaries": counts["alerts"] + counts["communities"], "from_api": 0,
        "from_template": counts["alerts"] + counts["communities"], "failed_checks": 0, "cached": 0}
    assert counts["alerts"] == 6 and counts["communities"] == 10

    # the push store: every community and alert has its evidence and hash; every one here has a template summary
    push = run.push_store()
    communities = rows(push, "MATCH (c:RiskCommunity) RETURN properties(c) AS p")
    alerts = rows(push, "MATCH (a:Alert) RETURN properties(a) AS p")
    assert len(communities) == 10 and len(alerts) == 6
    for r in (*communities, *alerts):
        p = r["p"]
        ev = Evidence.model_validate_json(p["evidence"])
        assert p["evidence_hash"] == ev.hash() == read_evidence(run, ev.key).hash()
        assert p["summary_source"] == "template" and "summary_model" not in p and "could indicate" in p["summary"]
        assert p["summary"] == template_summary(ev) == read_summary(run, ev.key).text
    assert set(communities[0]["p"]) <= set(COMMUNITY_PROPS) and set(alerts[0]["p"]) <= set(ALERT_PROPS)

    # the run store: each community's node holds its summary, and the full-text index finds the words in it
    nodes = run.table("MATCH (c:RiskCommunity) RETURN c.community_key AS k, c.summary AS summary")
    assert nodes.height == 10 and nodes["summary"].null_count() == 0
    by_key = {r["p"]["community_key"]: r["p"]["summary"] for r in communities}
    assert dict(zip(nodes["k"], nodes["summary"], strict=True)) == by_key
    create_indexes(run.store())
    found = lambda q: {r["k"] for r in rows(  # noqa: E731
        run.store(), "MATCH (c:RiskCommunity) SEARCH c IN (FULLTEXT INDEX community_summary FOR $q LIMIT 20) "
                     "SCORE AS score RETURN c.community_key AS k", {"q": q})}
    r2 = {k for k, text in by_key.items() if "instalment" in text}
    assert len(r2) == 1 and found("instalment") == r2  # the structuring ring: rent or an instalment plan
    assert len(found("household")) >= 2 and found("household") >= {k for k, t in by_key.items() if "household" in t}
    assert found("innocent") == set(by_key)  # every summary gives the innocent explanation

    # the manifest
    step = Run(run.id, run.paths.root.parent.parent).manifest.step("summaries")
    assert step.status == StepStatus.DONE and step.counts == {k: counts[k] for k in step.counts} and step.counts["summaries"] == 16
    assert step.started_at and step.finished_at and "template only" in step.note


def test_the_top_communities_and_alerts_by_score_get_summaries(world, monkeypatch):
    monkeypatch.setenv("LAB_SUMMARY_COMMUNITIES", "2")
    monkeypatch.setenv("LAB_SUMMARY_ALERTS", "3")
    reload_settings()
    shutil.rmtree(world.run.paths.artifacts / "summaries", ignore_errors=True)
    found, _, counts = attach_summaries(world.run, world.found, world.factor_run)
    assert counts == {"summaries": 5, "from_api": 0, "from_template": 5, "failed_checks": 0, "cached": 0}
    top_communities = world.communities.sort(pl.col("score"), pl.col("community_key"), descending=[True, False])[
        "community_key"].head(2).to_list()
    top_alerts = world.found.sort(pl.col("score"), pl.col("account_key"), descending=[True, False])["alert_key"].head(3).to_list()
    assert {p.stem for p in (world.run.paths.artifacts / "summaries").glob("*.json")} == {*top_communities, *top_alerts}
    # the returned alerts: evidence and hash on every row, summary columns on the top three only
    assert found.height == world.found.height and found["evidence_hash"].null_count() == 0
    assert found["summary"].null_count() == found.height - 3
    assert set(found.filter(pl.col("summary").is_not_null())["alert_key"]) == set(top_alerts)
    assert set(found.columns) <= set(ALERT_PROPS) and found.schema["summary_model"] == pl.Utf8
    for r in found.iter_rows(named=True):
        assert Evidence.model_validate_json(r["evidence"]).hash() == r["evidence_hash"]
    # evidence is saved for all of them, summaries or not
    assert len(list((world.run.paths.artifacts / "evidence").glob("*.json"))) == 10 + world.found.height


def test_the_step_marks_itself_failed_when_it_raises(fixture_run, monkeypatch):
    import lab.summaries

    def boom(*a, **k):
        raise RuntimeError("the writer broke")

    monkeypatch.setattr(lab.summaries, "summarise", boom)
    with pytest.raises(RuntimeError, match="the writer broke"):
        score(fixture_run)
    step = Run(fixture_run.id, fixture_run.paths.root.parent.parent).manifest.step("summaries")
    assert step.status == StepStatus.FAILED and "the writer broke" in step.note


def test_a_rerun_with_the_same_evidence_and_model_makes_no_call_through_the_whole_pipeline(fixture_run, monkeypatch):
    api = install(monkeypatch, ideal)
    first = score(fixture_run)
    assert (first["from_api"], first["cached"], first["from_template"], first["failed_checks"]) == (16, 0, 0, 0)
    assert len(api.calls) == 16 and len(api.built) == 1
    again = score(fixture_run)  # the same run, the same evidence, the same model
    assert len(api.calls) == 16 and len(api.built) == 1
    assert (again["from_api"], again["cached"], again["summaries"]) == (16, 16, 16)
    push = rows(fixture_run.push_store(), "MATCH (n) WHERE n.summary_source IS NOT NULL "
                                          "RETURN n.summary_source AS s, n.summary_model AS m")
    assert {(r["s"], r["m"]) for r in push} == {("api", MODEL)} and len(push) == 16
    assert "Anthropic API (claude-sonnet-5-5)" in Run(fixture_run.id, fixture_run.paths.root.parent.parent).manifest.step("summaries").note


def test_a_planted_number_in_one_answer_costs_only_that_summary(fixture_run, monkeypatch):
    def handler(kw):
        ev = Evidence.model_validate_json(kw["messages"][0]["content"].split("Evidence:\n", 1)[1])
        return with_planted_number(kw) if ev.subject == "alert" and ev.member_keys[0] == R1[0] else ideal(kw)

    install(monkeypatch, handler)
    counts = score(fixture_run)
    assert (counts["from_api"], counts["from_template"], counts["failed_checks"]) == (15, 1, 1)
    bad = rows(fixture_run.push_store(), "MATCH (a:Alert) WHERE a.summary_source = 'template' RETURN a.account_key AS k")
    assert [r["k"] for r in bad] == [R1[0]]


# ---- the agent's path ---------------------------------------------------------------------------------------------
def agent_text(ev: Evidence) -> str:
    return (
        f"The {ev.member_count} accounts in this community share device D-R1 and address AD-R1, and "
        f"{ev.money.internal:,.2f} moved between them. A pattern like this could indicate accounts run together rather "
        "than by independent customers. An innocent explanation to rule out is a household that shares one computer."
    )


def push_props(run, label: str, key_name: str, key: str) -> dict:
    return rows(run.push_store(), f"MATCH (n:{label} {{{key_name}: $k}}) RETURN properties(n) AS p", {"k": key})[0]["p"]


def test_describe_stores_good_agent_text_and_writes_it_everywhere_it_is_read(world):
    ev = community_evidence(world, R1)
    text = agent_text(ev)
    s = describe(world.run, ev.key, text)
    assert (s.source, s.model, s.text, s.evidence_hash, s.failed_reason) == ("agent", None, text, ev.hash(), None)
    assert all(s.checks.values()) and read_summary(world.run, ev.key) == s
    node = world.run.table("MATCH (c:RiskCommunity {community_key: $k}) RETURN c.summary AS s", {"k": ev.key})
    assert node["s"][0] == text  # the run store, so community_summary indexes it
    p = push_props(world.run, "RiskCommunity", "community_key", ev.key)
    assert (p["summary"], p["summary_source"], p["evidence_hash"]) == (text, "agent", ev.hash())
    assert "summary_model" not in p and p["created_at"].endswith("Z")
    create_indexes(world.run.store())
    hit = rows(world.run.store(), "MATCH (c:RiskCommunity) SEARCH c IN (FULLTEXT INDEX community_summary FOR $q LIMIT 5) "
                                  "SCORE AS score RETURN c.community_key AS k", {"q": "independent"})
    assert ev.key in {r["k"] for r in hit}


def test_describe_replaces_text_that_fails_a_check_with_the_template_and_says_why(world):
    ev = community_evidence(world, R2)
    good = template_summary(ev)
    for bad, word in ((good + " The accounts moved 4,321,987.65.", "4,321,987.65 is not in the evidence"),
                      ("Eight accounts share an email. That could indicate a scam. A household is possible.", "scam is a verdict word"),
                      (good + " Account A0001 is the boss.", "A0001 is not a member")):
        s = describe(world.run, ev.key, bad)
        assert s.source == "template" and s.model is None and s.text == template_summary(ev)
        assert s.failed_reason.startswith("agent text failed checks: ") and word in s.failed_reason
        assert not all(s.checks.values()) and s.evidence_hash == ev.hash()
        assert read_summary(world.run, ev.key) == s
        assert push_props(world.run, "RiskCommunity", "community_key", ev.key)["summary_source"] == "template"
        assert world.run.table("MATCH (c:RiskCommunity {community_key: $k}) RETURN c.summary AS s", {"k": ev.key})["s"][0] == s.text


def test_describe_takes_an_alert_key_or_the_key_of_an_account_that_has_an_alert(world):
    ev = alert_evidence(world)
    before = push_props(world.run, "Alert", "alert_key", ev.key)
    text = (f"Account {R1[0]} shares device D-R1, and {R1[0]} took in {ev.money.inflow:,.2f}. That could indicate the account "
            "is run together with others. An innocent explanation to rule out is a shared household computer.")
    by_alert = describe(world.run, ev.key, text)
    by_account = describe(world.run, R1[0], text.replace("shared household", "family"))
    assert by_alert.source == by_account.source == "agent" and by_alert.key == by_account.key == ev.key
    p = push_props(world.run, "Alert", "alert_key", ev.key)
    assert p["summary_source"] == "agent" and "family" in p["summary"] and "summary_model" not in p
    assert p["created_at"] == before["created_at"] and p["account_key"] == R1[0]  # the row was updated, not remade
    linked = rows(world.run.push_store(), "MATCH (:Alert {alert_key: $k})-[:ALERT_ON]->(a:Account) RETURN a.account_id AS a",
                  {"k": ev.key})
    assert [r["a"] for r in linked] == [R1[0]]
    assert len(rows(world.run.push_store(), "MATCH (a:Alert) RETURN a.alert_key AS k")) == world.found.height


def test_describe_for_an_unknown_key_is_a_lookup_error(world):
    with pytest.raises(LookupError, match="no evidence"):
        describe(world.run, "nonesuch", "Text. That could indicate x. An innocent cause.")
    with pytest.raises(LookupError):
        describe(world.run, "A0001", "Text. That could indicate x. An innocent cause.")  # an account with no alert


def test_an_agent_summary_replaces_an_api_summary_and_its_model_name_goes_with_it(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    install(monkeypatch, ideal)
    writer = Writer(cache=SummaryCache(tmp_path / "c.jsonl"), is_account=lookup(world))
    done = summarise(world.run, world.found, world.factor_run, writer=writer)
    world.run.stage(done.communities, "community_key", "community")  # `summarise` stages nothing: `score` does, once
    p = push_props(world.run, "RiskCommunity", "community_key", ev.key)
    assert (p["summary_source"], p["summary_model"]) == ("api", MODEL)
    describe(world.run, ev.key, agent_text(ev))
    p = push_props(world.run, "RiskCommunity", "community_key", ev.key)
    assert p["summary_source"] == "agent" and "summary_model" not in p  # staging cannot clear it; describe does
    other = push_props(world.run, "RiskCommunity", "community_key", community_of(world, R2))
    assert other["summary_model"] == MODEL  # the rest are untouched


# ---- more of the step ---------------------------------------------------------------------------------------------
def test_an_answer_in_a_models_own_words_is_stored_when_it_passes_and_gets_its_full_stops(world, monkeypatch, tmp_path):
    ev = community_evidence(world, R1)
    parts = {
        "shows": f"Six accounts, led by {R1[0]}, share device D-R1 and address AD-R1 and took in ${ev.money.inflow:,.2f}",
        "why_risk": "Opened together and tied by one device, they could indicate accounts run together by one party",
        "innocent_explanation": "An innocent explanation to rule out is a household that shares one computer",
    }
    install(monkeypatch, lambda kw: response(parts))
    written = Writer(cache=SummaryCache(tmp_path / "c.jsonl"), is_account=lookup(world)).write(ev)
    s = written.summary
    assert s.source == "api" and s.failed_reason is None and all(s.checks.values()), s.failed_reason
    assert s.text == " ".join(p + "." for p in parts.values())


def test_a_client_that_cannot_be_built_gives_the_template_with_a_reason(world, monkeypatch, tmp_path):
    def cannot(**kw):
        raise anthropic.AnthropicError("the key is not valid")

    install(monkeypatch, ideal)
    monkeypatch.setattr(anthropic, "Anthropic", cannot)
    s = Writer(cache=SummaryCache(tmp_path / "c.jsonl")).write(community_evidence(world, R1)).summary
    assert s.source == "template" and "could not open a client" in s.failed_reason and "not valid" not in s.failed_reason


def test_summarise_stages_nothing_and_hands_back_the_rows_with_their_evidence_and_summaries(world, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(world.run, "stage", lambda df, key=None, kind="account": calls.append(kind) or df.height)
    alerts, communities, counts, note = summarise(world.run, world.found, world.factor_run)
    assert calls == [] and counts["summaries"] == communities.height + alerts.height == 16 and "template only" in note
    assert {"community_key", "member_keys", "score", "size", "score_parts", "run_id", "created_at"} <= set(communities.columns)
    assert {"evidence", "evidence_hash", "summary", "summary_source", "summary_model"} <= set(communities.columns)
    assert "formed" not in communities.columns  # in the file and the score parts, not a staged column
    assert set(communities.columns) - {"member_keys"} <= set(COMMUNITY_PROPS) and set(alerts.columns) <= set(ALERT_PROPS)
    assert communities.height == 10 and alerts.height == world.found.height
    assert communities["summary"].null_count() == 0 and alerts["summary"].null_count() == 0


def test_the_stage_step_counts_each_alert_and_community_once(fixture_run):
    counts = score(fixture_run)
    step = Run(fixture_run.id, fixture_run.paths.root.parent.parent).manifest.step("stage")
    assert (step.counts["alerts"], step.counts["communities"], step.counts["accounts"]) == (counts["alerts"], counts["communities"], 200)
    assert (counts["alerts"], counts["communities"]) == (6, 10)  # the walk counted 20 and 236 for 10 and 118


@pytest.mark.parametrize(("threshold", "min_size", "alerts", "communities"), [
    ("0.99", "3", 0, 10), ("0.6", "1000", 6, 0), ("0.99", "1000", 0, 0),
])
def test_a_run_with_no_alerts_or_no_communities_is_fine(fixture_run, monkeypatch, tmp_path, threshold, min_size, alerts, communities):
    policy = tmp_path / "policy.yaml"
    text = (REPO / "factors" / "policy.yaml").read_text(encoding="utf-8")
    policy.write_text(text.replace("alert_threshold: 0.6", f"alert_threshold: {threshold}").replace("min_size: 3", f"min_size: {min_size}"),
                      encoding="utf-8")
    monkeypatch.setenv("LAB_POLICY_FILE", str(policy))
    reload_settings()
    counts = score(fixture_run)
    assert (counts["alerts"], counts["communities"], counts["summaries"]) == (alerts, communities, alerts + communities)
    assert counts["from_template"] == counts["summaries"]
    assert len(list((fixture_run.paths.artifacts / "evidence").glob("*.json"))) == alerts + communities
    assert Run(fixture_run.id, fixture_run.paths.root.parent.parent).manifest.step("summaries").status == StepStatus.DONE
