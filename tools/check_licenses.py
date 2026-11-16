"""No GPL in the image: every installed Python distribution's licence, from its own metadata.

    docker run --rm -v "$PWD/tools:/check:ro" subgraph-lab:dev python /check/check_licenses.py   # in the image
    uv run python tools/check_licenses.py                                    # in the dev environment

It reads `License`, `License-Expression` and the licence classifiers of every distribution in the running Python and
prints one line per distribution. A distribution whose licence names the GPL, the AGPL or the LGPL fails the check,
and so does one that says nothing: those need a person to read the licence file. Exit 1 when any fails.
"""
from __future__ import annotations

import re
import sys
from importlib import metadata

COPYLEFT = re.compile(r"\b(A?GPL|LGPL|GNU (Affero |Lesser )?General Public)", re.I)
# Distributions whose metadata says nothing, with what their licence file says (read by a person, with the date).
READ_BY_HAND: dict[str, str] = {
    # dist-info/LICENSE is MIT (PyG Team, 2022); LICENSE_radix_sort is BSD (FBGEMM). Read 15 November 2026, 0.7.0.
    "pyg_lib": "MIT; BSD (radix sort, from FBGEMM), read from the wheel's licence files",
}


def licence(dist: metadata.Distribution) -> str:
    m = dist.metadata
    parts = [m.get("License-Expression") or ""]
    lic = (m.get("License") or "").strip()
    if lic and len(lic) < 200:  # some put the whole licence text here; the classifiers say it better
        parts.append(lic)
    parts += [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
    return "; ".join(p for p in dict.fromkeys(parts) if p)


def main() -> int:
    bad = []
    rows = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"]
        text = licence(dist) or READ_BY_HAND.get((name or "").lower(), "")
        verdict = "ok"
        if not text:
            verdict = "UNKNOWN"
        elif COPYLEFT.search(text) and not re.search(r"\bor\b.*\b(MIT|BSD|Apache)", text, re.I):
            verdict = "COPYLEFT"
        rows.append((name, dist.version, verdict, text[:90]))
        if verdict != "ok":
            bad.append(name)
    for name, version, verdict, text in rows:
        print(f"{verdict:8} {name} {version}: {text}")
    print(f"{len(rows)} distributions, {len(bad)} to look at: {', '.join(bad) or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
