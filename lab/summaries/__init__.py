"""Evidence and summaries (page 05, track 3b): why a community or an alert could be risky, from checked evidence.

`evidence.py` the evidence object and its builder · `checks.py` the five checks and the verdict words · `template.py`
the template writer · `writers.py` the API writer, the agent writer and the rule that picks · `cache.py` the cache on
evidence hash plus model · `store.py` the files, the staged rows, the run-store text, and the step and the agent's path.

What other tracks call: `describe(run, key, text)` for `describe_alert`; `read_evidence(run, key)` and
`read_summary(run, key)` for `explain_alert`; `summarise` is the scoring step's (`attach_summaries`).
"""
from lab.summaries.cache import SummaryCache
from lab.summaries.checks import PARTS_HELP, VERDICT_WORDS, check
from lab.summaries.evidence import EvidenceContext, build_all, build_evidence, validate_evidence
from lab.summaries.store import describe, read_evidence, read_summary, summarise
from lab.summaries.template import template_summary
from lab.summaries.writers import Writer, agent_summary

__all__ = [
    "PARTS_HELP", "VERDICT_WORDS", "EvidenceContext", "SummaryCache", "Writer", "agent_summary", "build_all",
    "build_evidence", "check", "describe", "read_evidence", "read_summary", "summarise", "template_summary",
    "validate_evidence",
]
