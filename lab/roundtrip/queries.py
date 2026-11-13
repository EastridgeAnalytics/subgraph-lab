"""The statements a pull runs, built from the recipe's `PullSpec` and the graph mapping.

`relate pull --query` takes one statement or a `;`-separated list, and every statement must RETURN graph
elements. Schema mode builds two: the payments between accounts, then the identifier links of the accounts that
have a payment in the window. Names of labels, relationship types and properties come from the mapping, never
from this file. Parameters travel as `--param N=V`, which binds JSON scalars only.

The same statements run over Bolt in the fallback. There they also take a key range on the account key, so
parallel readers read disjoint slices (`key_range=True`).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from lab.contracts.graph import GraphMapping, IdentifierLink
from lab.contracts.recipe import PullSpec

Scalar = str | int | float | bool | None

KEY_LO, KEY_HI = "key_lo", "key_hi"  # the Bolt fallback's key-range parameters
SINCE, UNTIL = "since", "until"

_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def ident(name: str) -> str:
    """A label, relationship type or property name, in backticks only when Cypher needs them."""
    return name if _PLAIN.fullmatch(name) else "`" + name.replace("`", "``") + "`"


def literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


@dataclass(frozen=True)
class PullStatements:
    """What to run: the statements, and the parameters they bind."""

    statements: tuple[str, ...]
    params: dict[str, Scalar] = field(default_factory=dict)
    raw: str | None = None  # cypher mode: the query exactly as the recipe wrote it

    @property
    def query(self) -> str:
        """The text for `relate pull --query`."""
        return self.raw if self.raw is not None else ";\n".join(self.statements)

    def cli_params(self) -> list[str]:
        return param_args(self.params)


def param_args(params: dict[str, Scalar]) -> list[str]:
    """`--param N=V` pairs. Values are JSON, so the string "123" stays a string and 123 stays an integer."""
    args: list[str] = []
    for name, value in params.items():
        args += ["--param", f"{name}={json.dumps(value, ensure_ascii=False)}"]
    return args


def check_params(params: dict) -> dict[str, Scalar]:
    """`relate pull --param` binds JSON scalars only, so a list or a map is refused here, with its name."""
    for name, value in params.items():
        if not _PLAIN.fullmatch(str(name)):
            raise ValueError(f"pull param name {name!r} is not a plain identifier")
        if value is not None and not isinstance(value, str | int | float | bool):
            raise ValueError(
                f"pull param {name!r} is a {type(value).__name__}; relate pull --param binds JSON scalars only "
                "(string, number, boolean or null)"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"pull param {name!r} is {value}, which JSON cannot carry")
    return dict(params)


def split_statements(text: str) -> list[str]:
    """Split a `;`-separated statement list, leaving `;` inside strings, backticks and comments alone."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == "\\" and ch != "`":
                    j += 2
                    continue
                if text[j] == ch:
                    if ch == "`" and text[j + 1 : j + 2] == "`":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif ch == ";":
            out.append("".join(buf).strip())
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    out.append("".join(buf).strip())
    return [s for s in out if s]


def _iso(name: str, value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"pull.{name} must be ISO-8601, got {value!r}") from e
    return value


def selected_links(mapping: GraphMapping, spec: PullSpec) -> list[IdentifierLink]:
    """The identifier links a schema pull takes: those whose label and relationship type pass the filters.

    `labels` and `types` narrow the mapping; empty means everything in it. A name the mapping does not know is an
    error, and so is a filter that drops the account label or the payment type, since those are what the pull is.
    """
    known_labels = {mapping.account_label, *(link.label for link in mapping.identifiers)}
    known_types = {mapping.payment_type, *(link.rel_type for link in mapping.identifiers)}
    for what, given, known in (("labels", spec.labels, known_labels), ("types", spec.types, known_types)):
        unknown = [g for g in given if g not in known]
        if unknown:
            raise ValueError(
                f"pull.{what} names {unknown}, which the graph mapping does not know ({sorted(known)}); "
                "use a cypher pull for other shapes"
            )
    if spec.labels and mapping.account_label not in spec.labels:
        raise ValueError(f"pull.labels must include the account label {mapping.account_label!r}")
    if spec.types and mapping.payment_type not in spec.types:
        raise ValueError(f"pull.types must include the payment type {mapping.payment_type!r}")
    return [
        link
        for link in mapping.identifiers
        if (not spec.labels or link.label in spec.labels) and (not spec.types or link.rel_type in spec.types)
    ]


def account_keys_statement(mapping: GraphMapping) -> str:
    """The key list the Bolt fallback takes first and splits into ranges."""
    key = ident(mapping.account_key)
    return f"MATCH (a:{ident(mapping.account_label)}) WHERE a.{key} IS NOT NULL RETURN a.{key} AS k ORDER BY k"


def schema_statements(mapping: GraphMapping, spec: PullSpec, *, key_range: bool = False) -> PullStatements:
    """Schema mode: payments between accounts in the window, then the identifier links of those accounts.

    `since` is inclusive and `until` exclusive, both on the payment time property. An identifier link points from
    the account to the identifier. With `key_range`, both statements also take `$key_lo` and `$key_hi` on the
    account key (the source account for payments).
    """
    links = selected_links(mapping, spec)
    account, pay = ident(mapping.account_label), ident(mapping.payment_type)
    key, ts = ident(mapping.account_key), ident(mapping.payment_time)

    params: dict[str, Scalar] = {}
    window: list[str] = []
    if spec.since:
        params[SINCE] = _iso("since", spec.since)
        window.append(f"t.{ts} >= datetime(${SINCE})")
    if spec.until:
        params[UNTIL] = _iso("until", spec.until)
        window.append(f"t.{ts} < datetime(${UNTIL})")
    in_range = [f"a.{key} >= ${KEY_LO}", f"a.{key} <= ${KEY_HI}"] if key_range else []

    def where(*conditions: str) -> str:
        use = [c for c in conditions if c]
        return "WHERE " + " AND ".join(use) + "\n" if use else ""

    payments = (
        f"MATCH (a:{account})-[t:{pay}]->(b:{account})\n"
        + where(*in_range, *window)
        + "RETURN a, t, b"
    )
    statements = [payments]

    if links:
        inner = "MATCH (a)-[t:" + pay + "]-(:" + account + ")"
        if window:
            inner += " WHERE " + " AND ".join(window)
        has_payment = "EXISTS { " + inner + " }"
        if len(links) == 1:
            pattern = f"MATCH (a:{account})-[r:{ident(links[0].rel_type)}]->(x:{ident(links[0].label)})\n"
            pairing = ""
        else:
            types = "|".join(ident(link.rel_type) for link in links)
            pattern = f"MATCH (a:{account})-[r:{types}]->(x)\n"
            pairs = " OR ".join(
                f"(type(r) = {literal(link.rel_type)} AND x:{ident(link.label)})" for link in links
            )
            pairing = f"({pairs})"
        statements.append(pattern + where(*in_range, pairing, has_payment) + "RETURN a, r, x")
    return PullStatements(tuple(statements), params)


def cypher_statements(spec: PullSpec) -> PullStatements:
    """Cypher mode: the recipe's query as written, and its parameters, which must be JSON scalars."""
    if spec.mode != "cypher" or not spec.query:
        raise ValueError("cypher_statements needs a cypher pull with a query")
    statements = split_statements(spec.query)
    if not statements:
        raise ValueError("the pull query has no statement in it")
    return PullStatements(tuple(statements), check_params(spec.params), raw=spec.query)


def pull_statements(mapping: GraphMapping, spec: PullSpec) -> PullStatements:
    """What `relate pull` runs for this recipe."""
    return cypher_statements(spec) if spec.mode == "cypher" else schema_statements(mapping, spec)
