"""The checks on a summary's text (page 05, R18). Any failure sends the template instead.

`check(text, evidence, ...)` returns `(checks, reason)`: one boolean per check, True when it passed, and one line
naming what failed (None when nothing did). The same function runs on the API's text, on a connected agent's text
(`describe_alert`), and on a cached answer, so the three writers are held to one standard. Nothing here logs or
returns the text itself; a reason may name the offending number, word or account key.

| Check | Passes when |
|---|---|
| `numbers` | Every number in the text appears in the evidence. What "appears" means is `evidence_numbers` below |
| `members` | Every account the text names is a member of the evidence's community (an alert's member is its account) |
| `verdict_words` | No word from `VERDICT_WORDS` |
| `length` | At most `LAB_SUMMARY_WORDS` words, counted as whitespace-separated words |
| `parts` | The text has the three parts: see `PARTS_HELP` |

**What counts as a number that appears in the evidence.** The text's numbers are the digit strings left after the
evidence's own keys (member keys, shared-identifier keys) and its ISO dates are taken out, plus number words from
"two" up ("seven", "twenty", "hundred", "dozen"). Thousands separators are dropped, so `4,215.30` is 4215.30, and a
number followed by `%` is a percentage. A number appears when it equals, as a decimal, one of:

- a number in the evidence, as written (counts, scores, parts, money, span in hours, ranks, medians);
- a float of the evidence rounded to two or one decimals, or, when the float is 1 or more, to a whole number
  (4215.3 may be written 4215.3, 4215.30 or 4,215; 0.6234 may be written 0.62 or 0.6 but not 1);
- for a float between 0 and 1, the same value as a percentage, whole or to one decimal (0.6234 may be written 62%);
- the length of any list in the evidence (the members, the shared identifiers, the factors, a factor's evidence ids);
- a number, in digits or words, inside a factor's name in the evidence ("Payments just under 10,000" carries 10,000;
  "Five or more accounts on one device" carries 5).

An ISO date or time in the text must be the start of the evidence's `first_at` or `last_at` ("2026-10-01" is, and so is
"2026-10-01T09:10:00Z" when that is the stamp). Nothing else appears: a sum, a difference, hours turned into days, a
year on its own, a spelled-out date, and "one" or "zero" (too common as ordinary words to check) are the cases to
know. A number made by arithmetic on the evidence fails on purpose.

**What counts as an account named.** A word of the text that is a member key is fine. A word that is a shared
identifier's key in the evidence is fine (it is not an account). Any other word fails when it is an account in the
slice (`is_account`, which takes the candidate words and returns the ones that are account keys; no function means
this lookup is skipped) or when it has the shape of the members' keys and at least three characters ("A9999" among
members "A0011" and "A0012"), which catches an invented key. Purely numeric words are left to the numbers check.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from lab.contracts.evidence import Evidence
from lab.settings import get_settings

CHECKS = ("numbers", "members", "verdict_words", "length", "parts")

# The verdict words, in one place. A summary describes a pattern; it never says what a person is or did. The first
# ten are the brief's; the rest were added in 3b and are the project owner's to change. "fraud" and "mule" on their
# own are not here: a summary may name a pattern ("could indicate coordinated account opening") without accusing,
# and they would send most written answers to the template. Matching is whole words, any case, with an optional
# plural ending ("criminals", "scams").
VERDICT_WORDS = (
    "fraudster", "fraud ring", "criminal", "guilty", "laundering", "launderer", "scam", "scammer", "stole", "theft",
    "fraudulent", "thief", "thieves", "steal", "stealing", "stolen", "launder", "laundered", "convicted",
    "perpetrator", "culprit", "accomplice",
)

_VERDICT = re.compile(
    r"\b(?:" + "|".join(r"\s+".join(map(re.escape, w.split())) for w in sorted(VERDICT_WORDS, key=len, reverse=True))
    + r")(?:s|es)?\b",
    re.IGNORECASE,
)

# The three parts. Part one says what the evidence shows (any sentence that is not two or three), part two says
# why it could mean risk and must hedge, part three gives the innocent explanation to rule out and must say so.
_HEDGE = re.compile(
    r"\b(?:could|may|might|can)\s+(?:also\s+)?(?:indicate|mean|suggest|signal|point\s+to|reflect)\b", re.IGNORECASE
)
_INNOCENT = re.compile(
    r"\b(?:innocent|legitimate|benign|harmless|ordinary)\b|\brule[sd]?\s+out\b|\bexplained\s+by\b", re.IGNORECASE
)

PARTS_HELP = (
    "Three parts, in order, as at least three sentences. First: what the evidence shows. Second: why that pattern "
    "could mean risk, with a sentence that says it 'could indicate' (or 'may suggest') something. Third: the innocent "
    "explanation to rule out, in a sentence after the second that says 'innocent', 'legitimate', 'benign', 'ordinary', "
    "'rule out' or 'explained by'."
)

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?")
_THOUSANDS = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
_NUMBER = re.compile(rf"(?<![\d.,])({_THOUSANDS}|\d+(?:\.\d+)?)(%?)(?:st|nd|rd|th)?(?!\d)")
_TOKEN = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_\-.:/#]*[A-Za-z0-9_])?")
_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "million": 10**6, "dozen": 12,
}
_WORDS = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)

IsAccount = Callable[[Collection[str]], Collection[str]]


# ---- small helpers ---------------------------------------------------------------------------------------------
def count_words(text: str) -> int:
    return len(text.split())


def sentences(text: str) -> list[str]:
    """Sentences, split after `.`, `!` or `?` and a space. A decimal point is not followed by a space."""
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def verdict_words_in(text: str) -> list[str]:
    """The verdict words the text uses, lowercased, in order of appearance."""
    return [re.sub(r"\s+", " ", m.group(0).lower()) for m in _VERDICT.finditer(text)]


# ---- the numbers in the evidence ---------------------------------------------------------------------------------
def _dec(x: float | int | str) -> Decimal:
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(x)


def round_half_up(x: float | Decimal, places: int) -> Decimal:
    """`x` rounded to `places` decimals, halves up, from its printed value. The checks and the template both use it,
    so a number the template prints is always one the checks accept (Python's own formatting rounds halves to even)."""
    d = x if isinstance(x, Decimal) else _dec(x)
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _walk(obj: Any, plain: set[Decimal], percent: set[Decimal], in_factor_name: bool = False) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, int):
        plain.add(Decimal(obj))
    elif isinstance(obj, float):
        if not math.isfinite(obj):
            return
        d = _dec(obj)
        plain.add(d)
        plain.update({round_half_up(d, 2), round_half_up(d, 1)})
        if abs(d) >= 1:
            plain.add(round_half_up(d, 0))
        if 0 <= d <= 1:
            pct = d * 100
            percent.update({pct, round_half_up(pct, 1), round_half_up(pct, 0)})
    elif isinstance(obj, str):
        if in_factor_name:
            plain.update(Decimal(m.group(1).replace(",", "")) for m in _NUMBER.finditer(obj))
            plain.update(Decimal(_NUMBER_WORDS[m.group(1).lower()]) for m in _WORDS.finditer(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk(v, plain, percent, in_factor_name=in_factor_name or k == "name")
    elif isinstance(obj, list):
        plain.add(Decimal(len(obj)))
        for v in obj:
            _walk(v, plain, percent, in_factor_name)


def evidence_numbers(evidence: Evidence) -> tuple[set[Decimal], set[Decimal]]:
    """The numbers a summary may use: `(plain, percent)` as the module docstring says."""
    plain: set[Decimal] = set()
    percent: set[Decimal] = set()
    _walk(evidence.model_dump(mode="json"), plain, percent)
    return plain, percent


def _evidence_keys(evidence: Evidence) -> tuple[set[str], set[str]]:
    """The member keys and the shared identifiers' keys: the strings that are keys, not numbers or accounts to check."""
    members = set(evidence.member_keys) | {m.key for m in evidence.top_members}
    return members, {s.key for s in evidence.shared_identifiers}


def _strip_keys(text: str, keys: Collection[str]) -> str:
    if not keys:
        return text
    alt = "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
    return re.sub(rf"(?<![\w-])(?:{alt})(?![\w-])", " ", text)


# ---- the five checks ---------------------------------------------------------------------------------------------
def _numbers(text: str, evidence: Evidence, keys: Collection[str]) -> list[str]:
    plain, percent = evidence_numbers(evidence)
    stamps = [s for s in (evidence.money.first_at, evidence.money.last_at) if s]
    bad: list[str] = []
    body = _strip_keys(text, keys)
    for m in _DATE.finditer(body):
        if not any(s.startswith(m.group(0)) for s in stamps):
            bad.append(m.group(0))
    body = _DATE.sub(" ", body)
    for m in _NUMBER.finditer(body):
        value = Decimal(m.group(1).replace(",", ""))
        if value not in (percent if m.group(2) else plain):
            bad.append(m.group(0).strip())
    for m in _WORDS.finditer(body):
        if Decimal(_NUMBER_WORDS[m.group(1).lower()]) not in plain:
            bad.append(m.group(1).lower())
    return [f"{b} is not in the evidence" for b in dict.fromkeys(bad)]


def _shape(token: str) -> str:
    return re.sub(r"[A-Za-z]+", "a", re.sub(r"\d+", "9", token))


def _members(text: str, evidence: Evidence, is_account: IsAccount | None) -> list[str]:
    members, identifiers = _evidence_keys(evidence)
    shapes = {_shape(k) for k in members if any(c.isdigit() for c in k) and any(c.isalpha() for c in k)}
    shapes = shapes if len(shapes) <= 3 else set()  # keys too irregular for a shape to mean anything
    candidates = {
        t for t in _TOKEN.findall(text)
        if t not in members and t not in identifiers and not t.replace(".", "").isdigit()
    }
    named: set[str] = set()
    if is_account is not None:
        looks_like_key = {t for t in candidates if t != t.lower() or any(c in t for c in "0123456789-_:/#")}
        if looks_like_key:
            named |= set(is_account(looks_like_key))
    named |= {t for t in candidates if len(t) >= 3 and _shape(t) in shapes}
    return [f"{t} is not a member" for t in sorted(named)]


def _parts(text: str) -> list[str]:
    found = sentences(text)
    if len(found) < 3:
        return [f"{len(found)} sentences; the three parts need at least 3"]
    hedge = next((i for i, s in enumerate(found) if _HEDGE.search(s)), None)
    if hedge is None:
        return ["no sentence says what the pattern 'could indicate'"]
    if hedge == 0:
        return ["the sentence that says 'could indicate' comes first; what the evidence shows must come before it"]
    if not any(_INNOCENT.search(s) for s in found[hedge + 1:]):
        return ["no sentence after it gives the innocent explanation to rule out"]
    return []


def check(
    text: str, evidence: Evidence, *, is_account: IsAccount | None = None, max_words: int | None = None
) -> tuple[dict[str, bool], str | None]:
    """Run the five checks on `text` against `evidence`. Returns `(checks, reason)`; `reason` is None when all pass.

    `is_account` takes the candidate words of the text and returns the ones that are account keys in the slice, so a
    real account that is not a member is caught whatever its key looks like; see the module docstring. `max_words`
    defaults to `LAB_SUMMARY_WORDS`.
    """
    limit = get_settings().summary_words if max_words is None else max_words
    members, identifiers = _evidence_keys(evidence)
    problems: dict[str, list[str]] = {
        "numbers": _numbers(text, evidence, members | identifiers),
        "members": _members(text, evidence, is_account),
        "verdict_words": [f"{w} is a verdict word" for w in dict.fromkeys(verdict_words_in(text))],
        "length": [f"{count_words(text)} words; the limit is {limit}"] if count_words(text) > limit else [],
        "parts": _parts(text),
    }
    checks = {name: not problems[name] for name in CHECKS}
    failed = [f"{name}: {'; '.join(problems[name])}" for name in CHECKS if problems[name]]
    return checks, ("; ".join(failed) or None)
