"""The summary cache (page 05): answers the Anthropic API gave and the checks passed, keyed by evidence hash plus model.

One JSON line per answer in `summary_cache_path()`, shared across runs: `{"evidence_hash", "model", "text", "at"}`. A
re-run with the same evidence and the same model finds its answer here and makes no call. Only checked API text is
ever put in: the template is not cached as if it were an API answer, a failed answer is not cached, and an agent's
text is not cached (it has no model). The last line for a key wins. A line that cannot be read is skipped.

`evidence.hash()` includes the run id and the object's key, so two runs never share a hash: the cache serves a re-score
of the same run, not a new run of the same data. The key function is `cache_key`, the one place to change that.
"""
from __future__ import annotations

import json
from pathlib import Path

from lab.common import iso_utc, summary_cache_path


def cache_key(evidence_hash: str, model: str) -> tuple[str, str]:
    return evidence_hash, model


class SummaryCache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else summary_cache_path()
        self._rows: dict[tuple[str, str], str] | None = None

    def _load(self) -> dict[tuple[str, str], str]:
        if self._rows is None:
            self._rows = {}
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                        self._rows[cache_key(row["evidence_hash"], row["model"])] = row["text"]
                    except (ValueError, KeyError, TypeError):
                        continue
        return self._rows

    def get(self, evidence_hash: str, model: str) -> str | None:
        return self._load().get(cache_key(evidence_hash, model))

    def put(self, evidence_hash: str, model: str, text: str) -> None:
        rows = self._load()
        if rows.get(cache_key(evidence_hash, model)) == text:
            return
        rows[cache_key(evidence_hash, model)] = text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"evidence_hash": evidence_hash, "model": model, "text": text, "at": iso_utc()}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def __len__(self) -> int:
        return len(self._load())
