"""The template summary (page 05): three sentences built only from the evidence, no model, no key.

It is what a community or alert gets when no key is set, when no agent has written one, and when any check fails.
The same evidence always gives the same text, and the text passes `checks.check` (a test holds it to that).

1. **What the evidence shows.** The subject, its score and rank, the identifiers its members share, the money in, out
   and between members, the cycles, the first and last day, the factors that fired, the highest-scoring members. Every
   number is printed as the evidence holds it (counts as they are, scores to 2 decimals, money to 2 decimals).
2. **Why it could mean risk.** "A pattern like this could indicate" the two strongest of: accounts run together
   (shared identifiers), money moving in a loop (cycles), newly opened accounts, money passing straight through,
   members that already score high, payments held just under a round number, many accounts paying into one place.
   Strength is the community score's own part where the evidence has one, else 1 for a shared identifier or a loop
   and 0.8 for a factor cue.
3. **The innocent explanation to rule out.** Chosen from what the evidence shows, up to two, in this order: a factor
   about payments just under a round number (rent or an instalment plan); each shared identifier's kind (device: a
   household or a shared computer; address: people who share a home or an office; email: a shared family or business
   mailbox; phone: a shared phone line; any other kind: a shared resource several customers use legitimately; kinds in
   that order); a factor about fan-in or money in and out within a day (a payroll or collections account); money
   between members with none of those (relatives or housemates settling shared costs for up to `LAB_SUMMARY_SMALL_GROUP` members,
   a club, a team or a marketplace above that); else ordinary activity from a new business or a busy account. The factor cues are read from the factor's id and name, so a customer's own factor is a cue when it is
   named like one of these; a factor with no cue only shows in sentence one.

If the text is over `LAB_SUMMARY_WORDS` words, detail goes first (second explanation, then the last clauses of
sentence one) and the three sentences stay. A limit so small that the bare three sentences do not fit gets them anyway.
"""
from __future__ import annotations

from collections.abc import Iterable

from lab.contracts.evidence import Evidence
from lab.settings import get_settings
from lab.summaries.checks import count_words, round_half_up, verdict_words_in

_INNOCENT = {
    "device": "a household or a shared computer",
    "address": "people who share a home or an office",
    "email": "a shared family or business mailbox",
    "phone": "a shared phone line",
}
_KINDS = tuple(_INNOCENT)  # the order the explanations are offered in: the hardest link to fake first
_SHARED_OTHER = "a shared resource that several customers use legitimately"
_UNDER = "rent or an instalment plan paid in fixed amounts just below a round number"
_PAYROLL = "a payroll or collections account that takes money in and pays it straight on"
_HOUSEHOLD = "relatives or housemates settling shared costs"
_CLUB = "a club, a team or a marketplace of ordinary customers paying each other"
_ORDINARY = "ordinary activity from a new business or a busy account"


def _n(x: float, places: int = 2) -> str:
    return format(round_half_up(x, places), ",f")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def _join(items: Iterable[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _name(factor_id: str, name: str) -> str:
    """A factor's name for the text, or its id when the name would trip the verdict-word check."""
    return factor_id if verdict_words_in(name) else name


def _cue(evidence: Evidence, *needles: str) -> bool:
    """A factor that fired has one of `needles` in its id or its name (lowercased, with spaces and dashes as `_`)."""
    for f in evidence.factors:
        text = f"{f.factor_id} {f.name}".lower().replace("-", "_").replace(" ", "_")
        if any(n in text for n in needles):
            return True
    return False


def _shows(e: Evidence) -> list[str]:
    """The clauses of sentence one, most important first."""
    community = e.subject == "community"
    c = e.comparison
    if community:
        lead = (f"This community has {e.member_count} accounts and scores {_n(e.score)}, ranking {c.score_rank} "
                f"of {c.communities_in_slice} communities in the slice")
    else:
        lead = f"Account {e.member_keys[0]} scores {_n(e.score)}, ranking {c.score_rank} among the slice's alerts"
    clauses = [lead]
    if e.shared_identifiers:
        top = e.shared_identifiers[:2]
        how = (lambda s: f"{s.kind} {s.key} ({s.members_sharing} members)") if community else (
            lambda s: f"{s.kind} {s.key} (used by {s.members_sharing} accounts)")
        clauses.append(f"{'its members share' if community else 'it shares'} {_join(how(s) for s in top)}")
    m = e.money
    if m.first_at is None:
        clauses.append("no payments were found in the slice")
    else:
        if community:
            flows = f"took in {_n(m.inflow)}, paid out {_n(m.outflow)} and moved {_n(m.internal)} between members"
        else:
            flows = f"took in {_n(m.inflow)} and paid out {_n(m.outflow)}"
        clauses.append(f"{'they' if community else 'it'} {flows}")
    if m.cycles:
        count = f"{m.cycles:,} or more" if m.cycles >= get_settings().cycle_count_cap else str(m.cycles)
        clauses.append(f"money went round {count} payment {_plural(m.cycles, 'cycle')} among them")
    if m.first_at and m.last_at:
        clauses.append(f"payments ran from {m.first_at[:10]} to {m.last_at[:10]}")
    if e.factors:
        top = e.factors[:2]
        label = (lambda f: f"{_name(f.factor_id, f.name)} ({f.accounts} {_plural(f.accounts, 'account')})") if community else (
            lambda f: _name(f.factor_id, f.name))
        clauses.append(f"{_join(label(f) for f in top)} fired")
    if community and e.top_members and e.top_members[0].score > 0:
        clauses.append(f"the highest scores belong to {_join(m.key for m in e.top_members[:3])}")
    return clauses


def _risks(e: Evidence) -> str:
    parts = e.score_parts
    strength = {
        "accounts run together rather than by independent customers":
            parts.get("shared_identifiers", 1.0 if e.shared_identifiers else 0.0),
        "money moving in a loop between linked accounts": parts.get("cycles", 1.0 if e.money.cycles else 0.0),
        "a batch of newly opened accounts": parts.get("new_accounts", 0.0),
        "money passing straight through the accounts":
            max(parts.get("speed", 0.0), 0.8 if _cue(e, "in_and_out", "pass_through") else 0.0),
        "members that already score high on their own": parts.get("member_risk", 0.0) if e.subject == "community" else 0.0,
        "payments kept just under a round number": 0.8 if _cue(e, "just_under") else 0.0,
        "many accounts paying into one place": 0.8 if _cue(e, "fan_in") else 0.0,
    }
    ranked = [p for p, s in sorted(strength.items(), key=lambda kv: -kv[1]) if s > 0][:2]
    return _join(ranked) if ranked else "unusual activity that the data alone cannot explain"


def _innocent(e: Evidence) -> list[str]:
    out: list[str] = []
    if _cue(e, "just_under"):
        out.append(_UNDER)
    for s in sorted(e.shared_identifiers, key=lambda s: _KINDS.index(s.kind) if s.kind in _KINDS else len(_KINDS)):
        say = _INNOCENT.get(s.kind, _SHARED_OTHER)
        if say not in out:
            out.append(say)
    if _cue(e, "fan_in", "in_and_out", "pass_through"):
        out.append(_PAYROLL)
    if not out and (e.money.internal > 0 or e.money.cycles):
        out.append(_HOUSEHOLD if e.member_count <= get_settings().summary_small_group else _CLUB)
    return (out or [_ORDINARY])[:2]


def _compose(shows: list[str], risks: str, innocent: list[str]) -> str:
    first = shows[0] + "".join(f"; {c}" for c in shows[1:]) + "."
    second = f"A pattern like this could indicate {risks}."
    third = f"An innocent explanation to rule out first is {', or '.join(innocent)}."
    return f"{first} {second} {third}"


def template_summary(evidence: Evidence, max_words: int | None = None) -> str:
    """The three-sentence summary of `evidence`, at most `max_words` words (default `LAB_SUMMARY_WORDS`)."""
    limit = get_settings().summary_words if max_words is None else max_words
    shows, risks, innocent = _shows(evidence), _risks(evidence), _innocent(evidence)
    text = _compose(shows, risks, innocent)
    while count_words(text) > limit and (len(innocent) > 1 or len(shows) > 1):
        if len(innocent) > 1:
            innocent = innocent[:1]
        else:
            shows = shows[:-1]
        text = _compose(shows, risks, innocent)
    return text
