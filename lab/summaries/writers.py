"""The three writers of a summary (page 05) and the rule that picks between them.

| Writer | When | What happens to its text |
|---|---|---|
| Anthropic API | `ANTHROPIC_API_KEY` is set | Looked up in the cache first. Else one call; the answer is checked; a pass is cached and stored with source `api`, a failure stores the template with the reason |
| Agent | A connected agent sends text through `describe_alert` (`describe` in `store.py`, which uses `agent_summary` below) | The same checks; a pass is stored with source `agent`, a failure stores the template with the reason |
| Template | No key, or any failure above | `template.template_summary`, built from the same evidence. Source `template`, no model |

`Writer.write(evidence)` is the first row and the last. **With no key it never builds a client or opens a
connection**: `anthropic.Anthropic` is only constructed on the first cache miss with a key set. The key is never logged,
and neither is the evidence or any text.

The call is `client.messages.create(model=settings.model, max_tokens=4000, system=SYSTEM_PROMPT, messages=[...],
output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}})`. It sets no
`temperature`, `top_p`, `top_k`, `thinking`, `budget_tokens`, prefill or `tool_choice`: current models answer those with a
400. The answer is JSON with `shows`, `why_risk` and `innocent_explanation`; the three are joined into the text.

Failures all end in the template, with a `failed_reason`: a refusal, `max_tokens`, a context-window stop, a rate limit,
any other status error, a connection error, an answer that is not the JSON asked for, and any failed check. The SDK has
already retried twice before an error reaches here. After `MAX_API_ERRORS` errors in a row the writer stops calling
for the rest of its life (one `score` step) and says so, so an unreachable API costs three calls and not one per subject.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic

from lab.common import canonical_json, get_logger
from lab.contracts.evidence import Evidence, Summary
from lab.settings import Settings, get_settings
from lab.summaries.cache import SummaryCache
from lab.summaries.checks import PARTS_HELP, IsAccount, check
from lab.summaries.template import template_summary

log = get_logger("summaries.writers")

MAX_TOKENS = 4000
MAX_API_ERRORS = 3
PART_KEYS = ("shows", "why_risk", "innocent_explanation")

SYSTEM_PROMPT = """You write the reason a group of accounts, or one account, could be risky, for a person who reviews \
alerts. You are given one evidence object as JSON. It holds keys and totals, never names. Write only from it.

Rules. Every one is checked after you write, and a summary that breaks one is thrown away.
- Describe the pattern, not the people. Never say what a person or an account is or did. Use "could indicate" or \
"may suggest", never a verdict. Never use words such as fraudster, criminal, guilty, scam, theft, laundering or \
"fraud ring".
- Every number you write must be a number in the evidence, written as the evidence has it: counts as they are, \
scores to 2 decimals, money as given or to whole units, hours as hours. Do not add, subtract, convert or estimate. \
Write a date only as it appears in the evidence (2026-10-01). Do not write a year, a month or a number of days on its own.
- Name an account only by a key from member_keys. Do not name any other account, a payment id or an evidence id.
- Always give the innocent explanation to rule out, chosen from what the evidence shows: a shared device is a \
household or a shared computer, an address is people sharing a home or an office, payments just under a round number \
are rent or instalments, many payments in from one place are payroll.
- Be short: keep to the word limit in the request."""

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "shows": {"type": "string", "description": "What the evidence shows: the facts and numbers, from the evidence only."},
        "why_risk": {
            "type": "string",
            "description": "Why that pattern could mean risk. Say what it 'could indicate'. Describe the pattern, not people.",
        },
        "innocent_explanation": {
            "type": "string",
            "description": "The innocent explanation to rule out. Begin 'An innocent explanation to rule out is'.",
        },
    },
    "required": list(PART_KEYS),
    "additionalProperties": False,
}


class WriterFailed(Exception):
    """The API gave no usable text. `reason` says why, for `Summary.failed_reason`."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def user_message(evidence: Evidence, settings: Settings | None = None) -> str:
    words = (settings or get_settings()).summary_words
    return (
        f"Write the summary of this evidence as JSON with the keys {', '.join(PART_KEYS)}. {PARTS_HELP} "
        f"At most {words} words in all.\n\nEvidence:\n{canonical_json(evidence.model_dump(mode='json'))}"
    )


_STOPPED = {
    "refusal": "api: the model refused",
    "max_tokens": "api: the answer stopped at max_tokens",
    "model_context_window_exceeded": "api: the evidence exceeded the context window",
}


def api_summary_text(client: Any, evidence: Evidence, settings: Settings | None = None) -> str:
    """One call to the API for `evidence`. Returns the three parts joined, unchecked; raises `WriterFailed`."""
    s = settings or get_settings()
    try:
        response = client.messages.create(
            model=s.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message(evidence, s)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        )
    except anthropic.RateLimitError as e:
        raise WriterFailed("api: rate limited (429)") from e
    except anthropic.APIStatusError as e:
        raise WriterFailed(f"api: status {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        raise WriterFailed("api: connection error") from e
    except anthropic.APIError as e:
        raise WriterFailed(f"api: {type(e).__name__}") from e
    stopped = getattr(response, "stop_reason", None)
    if stopped in _STOPPED:
        raise WriterFailed(_STOPPED[stopped])
    block = next((b for b in getattr(response, "content", []) if getattr(b, "type", None) == "text"), None)
    if block is None:
        raise WriterFailed("api: the answer has no text block")
    try:
        data = json.loads(block.text)
    except (ValueError, TypeError) as e:
        raise WriterFailed("api: the answer is not JSON") from e
    parts = [data.get(k) for k in PART_KEYS] if isinstance(data, dict) else []
    if len(parts) != len(PART_KEYS) or not all(isinstance(p, str) and p.strip() for p in parts):
        raise WriterFailed("api: the answer does not have the three parts asked for")
    return " ".join(p.strip() if p.strip()[-1] in ".!?" else p.strip() + "." for p in parts)


@dataclass
class Written:
    """A summary and how it came about, for the counts."""

    summary: Summary
    cached: bool = False
    failed_check: bool = False


def template_for(
    evidence: Evidence, reason: str | None = None, checks: dict[str, bool] | None = None,
    is_account: IsAccount | None = None, settings: Settings | None = None,
) -> Summary:
    """The template as a `Summary`. `reason` and `checks` are the failed text's, when a text was replaced; else the
    checks are the template's own, recorded and not enforced (the template is what is stored whatever they say)."""
    s = settings or get_settings()
    text = template_summary(evidence, s.summary_words)
    if checks is None:
        checks, _ = check(text, evidence, is_account=is_account, max_words=s.summary_words)
    return Summary(
        key=evidence.key, text=text, source="template", model=None, evidence_hash=evidence.hash(),
        checks=checks, failed_reason=reason,
    )


def agent_summary(
    evidence: Evidence, text: str, is_account: IsAccount | None = None, settings: Settings | None = None
) -> Summary:
    """A connected agent's text, checked: stored as source `agent` when it passes, else the template with the reason."""
    s = settings or get_settings()
    text = text.strip()
    checks, why = check(text, evidence, is_account=is_account, max_words=s.summary_words)
    if why is None:
        return Summary(
            key=evidence.key, text=text, source="agent", model=None, evidence_hash=evidence.hash(), checks=checks
        )
    return template_for(evidence, f"agent text failed checks: {why}", checks, is_account, s)


class Writer:
    """Writes one summary per evidence object: the cache, the API, the checks, the template."""

    def __init__(
        self, settings: Settings | None = None, cache: SummaryCache | None = None, is_account: IsAccount | None = None
    ):
        self.settings = settings or get_settings()
        self.cache = cache if cache is not None else SummaryCache()
        self.is_account = is_account
        self._client: Any = None
        self._errors = 0

    @property
    def uses_api(self) -> bool:
        return bool(self.settings.anthropic_api_key)

    def _api_client(self) -> Any:
        if self._client is None:
            try:
                self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
            except anthropic.AnthropicError as e:
                raise WriterFailed(f"api: could not open a client ({type(e).__name__})") from e
        return self._client

    def write(self, evidence: Evidence) -> Written:
        s, model = self.settings, self.settings.model
        if not self.uses_api:
            return Written(template_for(evidence, None, None, self.is_account, s))
        evidence_hash = evidence.hash()
        cached = self.cache.get(evidence_hash, model)
        if cached is not None:  # checked again: the checks may have changed since the answer was cached
            checks, why = check(cached, evidence, is_account=self.is_account, max_words=s.summary_words)
            if why is None:
                return Written(
                    Summary(key=evidence.key, text=cached, source="api", model=model, evidence_hash=evidence_hash, checks=checks),
                    cached=True,
                )
        if self._errors >= MAX_API_ERRORS:
            return Written(template_for(
                evidence, f"api: not called, the last {MAX_API_ERRORS} calls failed", None, self.is_account, s))
        try:
            text = api_summary_text(self._api_client(), evidence, s)
        except WriterFailed as e:
            self._errors += 1
            log.warning("summary for %s: %s", evidence.key, e.reason)
            return Written(template_for(evidence, e.reason, {}, self.is_account, s))
        self._errors = 0
        checks, why = check(text, evidence, is_account=self.is_account, max_words=s.summary_words)
        if why is not None:
            log.warning("summary for %s failed its checks: %s", evidence.key, [k for k, ok in checks.items() if not ok])
            return Written(template_for(evidence, f"checks failed: {why}", checks, self.is_account, s), failed_check=True)
        self.cache.put(evidence_hash, model, text)
        return Written(Summary(key=evidence.key, text=text, source="api", model=model, evidence_hash=evidence_hash, checks=checks))
