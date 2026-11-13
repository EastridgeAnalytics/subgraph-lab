"""Build the synthetic payments graph in memory, with numpy only.

One call, `build(size, seed)`, returns a `Graph`: account, device, email and address ids, the links between them,
the payments, and the ground truth. Everything is drawn from numpy generators spawned from one `SeedSequence`, so
the same seed and size give the same graph, and a change to one pattern leaves the others alone.

The graph has three kinds of account:
  * planted fraud, in groups with a ring id: mule rings, structuring, rapid pass-through (page 04);
  * legitimate look-alikes, in groups with a look-alike id: family, payroll, landlord (page 04);
  * everyone else: ordinary payments, an own device, email and address.

Every account belongs to at most one group. Roles are dealt from a random permutation, so an account's id says
nothing about its role. Payments between two accounts of the same group are only the pattern's own payments.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

DAY = 86_400
HOUR = 3_600
MINUTE = 60

# The time frame is fixed so a seed gives the same graph on any day.
END = int(datetime(2026, 11, 1, tzinfo=UTC).timestamp())  # payments run up to here
WINDOW_DAYS = 184  # 1 May to 1 November 2026
START = END - WINDOW_DAYS * DAY
END_DAY = END // DAY
OPEN_SPAN_DAYS = 730  # ordinary accounts were opened over about two years
NEW_ACCOUNT_DAYS = (5, 35)  # a planted ring member was opened this many days before its ring started
FRAUD_FIRST_DAY = END_DAY - 75  # rings start in the last 75 days

STRUCTURING_THRESHOLD = 10_000.0
ACCOUNT_PREFIX, PAYMENT_PREFIX = "A", "T"
MONTH_STARTS = [int(datetime(2026, m, 1, tzinfo=UTC).timestamp()) for m in range(5, 11)]  # May to October
RENTS = np.array([1195.0, 1495.0, 1695.0, 1795.0, 1995.0, 2195.0, 2495.0, 2995.0])  # just under a round number

FRAUD_PATTERNS = ("mule_ring", "structuring", "pass_through")
LOOKALIKE_PATTERNS = ("family", "payroll", "landlord")
PATTERNS = FRAUD_PATTERNS + LOOKALIKE_PATTERNS
PATTERN_SIZES = {  # accounts in one group, inclusive range
    "mule_ring": (4, 8),
    "structuring": (5, 10),  # the collector and 4 to 9 payers
    "pass_through": (3, 5),
    "family": (2, 5),
    "payroll": (21, 61),  # the payer and 20 to 60 employees
    "landlord": (6, 16),  # the landlord and 5 to 15 tenants
}


@dataclass(frozen=True)
class SizeSpec:
    accounts: int
    relationships: int  # the total of every relationship type, hit exactly
    groups: int  # groups of each of the six patterns


# `small` stays under Aura Free's smaller published limit (50,000 nodes, 175,000 relationships): at most four
# nodes per account, exactly three identifier links per account.
SIZES = {
    "small": SizeSpec(accounts=10_000, relationships=150_000, groups=20),
    "1m": SizeSpec(accounts=70_000, relationships=1_000_000, groups=140),
}


@dataclass
class Graph:
    account_ids: np.ndarray  # "A0000001", in order
    opened_days: np.ndarray  # days since 1970-01-01
    device_of: np.ndarray  # per account, an index into device_ids
    email_of: np.ndarray
    address_of: np.ndarray
    device_ids: np.ndarray
    email_ids: np.ndarray
    address_ids: np.ndarray
    tx_id: np.ndarray
    tx_src: np.ndarray  # account indexes
    tx_dst: np.ndarray
    tx_amount: np.ndarray
    tx_ts: np.ndarray  # epoch seconds
    ring_id: np.ndarray  # per account, "" when none
    pattern: np.ndarray
    lookalike_id: np.ndarray
    lookalike_pattern: np.ndarray
    groups: dict[str, int]  # groups planted, by pattern
    group_accounts: dict[str, int]  # accounts in them, by pattern


class _Events:
    """Payments collected as arrays, then joined."""

    def __init__(self) -> None:
        self._parts: list[tuple[np.ndarray, ...]] = []

    def add(self, src, dst, amount, ts, keep=None) -> None:
        """Scalars broadcast against arrays. `keep` is an optional mask of the payments to record."""
        n = max(np.size(src), np.size(dst), np.size(amount), np.size(ts))
        cols = [np.broadcast_to(np.asarray(c, dtype=t), (n,)) for c, t in
                ((src, np.int64), (dst, np.int64), (amount, np.float64), (ts, np.int64))]
        if keep is not None:
            cols = [c[keep] for c in cols]
        if len(cols[0]):
            self._parts.append(tuple(cols))

    def count(self) -> int:
        return sum(len(p[0]) for p in self._parts)

    def arrays(self) -> tuple[np.ndarray, ...]:
        return tuple(np.concatenate([p[i] for p in self._parts]) for i in range(4))


def _money(x) -> np.ndarray:
    return np.round(np.asarray(x, dtype=np.float64), 2)


def _ids(prefix: str, numbers: np.ndarray) -> np.ndarray:
    return np.char.add(prefix, np.char.zfill(numbers.astype(str), 7))


def _start_day(rng: np.random.Generator, span_days: int) -> int:
    """A first day for a fraud group, so that `span_days` of activity ends before the window does."""
    return int(rng.integers(FRAUD_FIRST_DAY, END_DAY - span_days))


def _mule_ring(rng, members, pool, opened, ev) -> None:
    """New accounts on one device and one address. Money in from outsiders, then a fast cycle, repeated."""
    k = len(members)
    rounds, gap = int(rng.integers(3, 9)), int(rng.integers(2, 7))
    day = _start_day(rng, rounds * gap + 1)
    opened[members] = day - rng.integers(*NEW_ACCOUNT_DAYS, size=k, endpoint=True)
    for j in range(rounds):
        start = (day + j * gap) * DAY + int(rng.integers(8 * HOUR, 14 * HOUR))
        base = rng.uniform(1_500, 6_000)
        n_in = int(rng.integers(k, 2 * k + 1))
        ev.add(rng.choice(pool, n_in), rng.choice(members, n_in), _money(base * rng.uniform(0.5, 1.5, n_in)),
               start + rng.integers(0, 90 * MINUTE, n_in))
        order = rng.permutation(members)
        hops = start + 2 * HOUR + np.cumsum(rng.integers(10 * MINUTE, 60 * MINUTE, k))
        ev.add(order, np.roll(order, -1), _money(base * 0.98 ** np.arange(k)), hops)


def _structuring(rng, members, pool, opened, ev) -> None:
    """Members share one email. Each pays the collector just under the threshold, repeatedly; the collector pays
    out a large sum."""
    collector, payers = members[0], members[1:]
    repeats, gap = int(rng.integers(4, 9)), int(rng.integers(1, 4))
    day = _start_day(rng, repeats * gap + 1)
    opened[members] = day - rng.integers(*NEW_ACCOUNT_DAYS, size=len(members), endpoint=True)
    for j in range(repeats):
        t = (day + j * gap) * DAY
        paying = rng.random(len(payers)) < 0.85
        paying[:3] = True  # at least three payers, so the payout is large
        amounts = _money(rng.uniform(0.94 * STRUCTURING_THRESHOLD, 0.999 * STRUCTURING_THRESHOLD, paying.sum()))
        ev.add(payers[paying], collector, amounts, t + 9 * HOUR + rng.integers(0, 5 * HOUR, paying.sum()))
        ev.add(collector, rng.choice(pool), _money(amounts.sum() * rng.uniform(0.92, 0.99)),
               t + 18 * HOUR + int(rng.integers(0, 2 * HOUR)))


def _pass_through(rng, members, pool, opened, ev) -> None:
    """A chain: money in from an outsider, forwarded hop by hop within minutes, nearly the same amount."""
    c = len(members)
    repeats, gap = int(rng.integers(4, 11)), int(rng.integers(1, 6))
    day = _start_day(rng, repeats * gap + 1)
    opened[members] = day - rng.integers(*NEW_ACCOUNT_DAYS, size=c, endpoint=True)
    for j in range(repeats):
        t = (day + j * gap) * DAY + int(rng.integers(8 * HOUR, 18 * HOUR))
        first, last = rng.choice(pool, 2, replace=False)
        amounts = _money(rng.uniform(2_000, 25_000) * np.cumprod(np.r_[1.0, rng.uniform(0.97, 0.995, c)]))
        times = t + np.r_[0, np.cumsum(rng.integers(2 * MINUTE, 11 * MINUTE, c))]
        ev.add(np.r_[first, members], np.r_[members, last], amounts, times)


def _timestamps(rng, lo: np.ndarray) -> np.ndarray:
    """Uniform between `lo` and the end of the window, one per element of `lo`."""
    return lo + (rng.random(len(lo)) * (END - lo)).astype(np.int64)


def _family(rng, members, opened, ev) -> None:
    """A few accounts on one device and address, small payments between them."""
    f, n = len(members), int(rng.integers(15, 61))
    i = rng.integers(0, f, n)
    a, b = members[i], members[(i + rng.integers(1, f, n)) % f]
    lo = np.maximum(START, np.maximum(opened[a], opened[b]) * DAY)
    ev.add(a, b, _money(np.clip(rng.lognormal(3.8, 0.8, n), 5, 400)), _timestamps(rng, lo))


def _payroll(rng, members, opened, ev) -> None:
    """One payer, 20 to 60 employees, the same amount to each on the same day every month."""
    payer, staff = members[0], members[1:]
    amount, day = float(_money(rng.uniform(1_200, 4_800))), int(rng.integers(1, 29))
    for month in MONTH_STARTS:
        ts = month + (day - 1) * DAY + 6 * HOUR + rng.integers(0, 2 * MINUTE, len(staff))
        ev.add(payer, staff, amount, ts, keep=ts >= opened[staff] * DAY)


def _landlord(rng, members, opened, ev) -> None:
    """One landlord, 5 to 15 tenants, each paying rent just under a round number every month."""
    landlord, tenants = members[0], members[1:]
    t = len(tenants)
    rents = rng.choice(RENTS, size=int(rng.integers(1, 3)), replace=False)
    rent, due = rng.choice(rents, t), rng.integers(1, 6, t)
    for month in MONTH_STARTS:
        ts = month + (due - 1) * DAY + 8 * HOUR + rng.integers(0, 12 * HOUR, t)
        ev.add(tenants, landlord, rent, ts, keep=ts >= opened[tenants] * DAY)


def _background(rng, n, n_accounts, opened, group) -> tuple[np.ndarray, ...]:
    """`n` ordinary payments between any two accounts of different groups.

    Busier accounts are heavy-tailed, an account is as busy as the share of the window it has been open, and a
    payment never comes before either account opened.
    """
    active = np.clip((END - np.maximum(START, opened * DAY)) / (END - START), 0.02, 1.0)
    p_out = active * np.minimum(rng.lognormal(0, 0.9, n_accounts), 20)
    p_in = active * np.minimum(rng.lognormal(0, 0.9, n_accounts), 20)
    p_out, p_in = p_out / p_out.sum(), p_in / p_in.sum()
    src, dst = rng.choice(n_accounts, n, p=p_out), rng.choice(n_accounts, n, p=p_in)
    while (bad := (src == dst) | ((group[src] == group[dst]) & (group[src] >= 0))).any():
        dst[bad] = rng.choice(n_accounts, int(bad.sum()), p=p_in)
    amount = np.clip(rng.lognormal(4.3, 1.0, n), 1.0, 5_000.0)
    big = rng.random(n) < 0.015  # a few large legitimate payments, some of them near the threshold
    amount[big] = rng.uniform(5_000, 30_000, int(big.sum()))
    lo = np.maximum(START, np.maximum(opened[src], opened[dst]) * DAY)
    return src, dst, _money(amount), _timestamps(rng, lo)


def build(size: str, seed: int) -> Graph:
    spec = SIZES[size]
    n = spec.accounts
    r_roles, r_accounts, r_patterns, r_background = (
        np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(4)
    )

    # Phase 1: who is in which group. Roles come from a random permutation of the account indexes.
    perm = r_roles.permutation(n)
    cursor = 0
    group = np.full(n, -1, dtype=np.int64)
    ring_id = np.full(n, "", dtype="<U8")
    pattern = np.full(n, "", dtype="<U12")
    lookalike_id = np.full(n, "", dtype="<U8")
    lookalike_pattern = np.full(n, "", dtype="<U12")
    planted: list[tuple[str, np.ndarray]] = []
    ring_no = look_no = 0
    for name in PATTERNS:
        lo, hi = PATTERN_SIZES[name]
        for _ in range(spec.groups):
            members = perm[cursor:cursor + int(r_roles.integers(lo, hi + 1))]
            cursor += len(members)
            group[members] = len(planted)
            planted.append((name, members))
            if name in FRAUD_PATTERNS:
                ring_no += 1
                ring_id[members], pattern[members] = f"R{ring_no:04d}", name
            else:
                look_no += 1
                lookalike_id[members], lookalike_pattern[members] = f"L{look_no:04d}", name
    if cursor > n * 0.4:
        raise ValueError(f"size {size!r}: {cursor} planted accounts leave too few ordinary ones")

    # Identifiers: everyone has their own, except where a pattern shares one.
    device_key, email_key, address_key = (np.arange(n) for _ in range(3))
    for name, members in planted:
        if name in ("mule_ring", "family"):
            device_key[members] = address_key[members] = members.min()
        elif name == "structuring":
            email_key[members] = members.min()

    # Opening dates: ordinary accounts over about two years, none in the last 30 days.
    opened = END_DAY - r_accounts.integers(30, OPEN_SPAN_DAYS + 1, size=n)
    pool = np.flatnonzero((group == -1) & (opened <= END_DAY - 90))  # outsiders: established ordinary accounts
    for name, members in planted:  # a payer and a landlord are established before the window opens
        if name in ("payroll", "landlord"):
            opened[members[0]] = END_DAY - r_accounts.integers(400, OPEN_SPAN_DAYS + 1)

    # Phase 2: the patterns' own payments. Fraud groups reset their members' opening dates to "recent".
    ev = _Events()
    fraud = {"mule_ring": _mule_ring, "structuring": _structuring, "pass_through": _pass_through}
    for name, members in planted:
        if name == "family":
            _family(r_patterns, members, opened, ev)
        elif name == "payroll":
            _payroll(r_patterns, members, opened, ev)
        elif name == "landlord":
            _landlord(r_patterns, members, opened, ev)
        else:
            fraud[name](r_patterns, members, pool, opened, ev)

    # Ordinary payments fill the relationship budget exactly: three identifier links per account, the rest TX.
    n_background = spec.relationships - 3 * n - ev.count()
    if n_background < 0:
        raise ValueError(f"size {size!r}: the patterns alone exceed {spec.relationships} relationships")
    bg = _background(r_background, n_background, n, opened, group)
    src, dst, amount, ts = (np.concatenate([p, b]) for p, b in zip(ev.arrays(), bg, strict=True))
    order = np.argsort(ts, kind="stable")  # payment ids follow time, not role
    src, dst, amount, ts = src[order], dst[order], amount[order], ts[order]

    def nodes(key: np.ndarray, prefix: str) -> tuple[np.ndarray, np.ndarray]:
        unique, inverse = np.unique(key, return_inverse=True)
        return inverse.reshape(-1), _ids(prefix, np.arange(1, len(unique) + 1))

    device_of, device_ids = nodes(device_key, "D")
    email_of, email_ids = nodes(email_key, "E")
    address_of, address_ids = nodes(address_key, "AD")
    return Graph(
        account_ids=_ids(ACCOUNT_PREFIX, np.arange(1, n + 1)), opened_days=opened,
        device_of=device_of, email_of=email_of, address_of=address_of,
        device_ids=device_ids, email_ids=email_ids, address_ids=address_ids,
        tx_id=_ids(PAYMENT_PREFIX, np.arange(1, len(ts) + 1)), tx_src=src, tx_dst=dst, tx_amount=amount, tx_ts=ts,
        ring_id=ring_id, pattern=pattern, lookalike_id=lookalike_id, lookalike_pattern=lookalike_pattern,
        groups={p: spec.groups for p in PATTERNS},
        group_accounts={p: sum(len(m) for nm, m in planted if nm == p) for p in PATTERNS},
    )
