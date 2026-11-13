"""Write the tiny fixture graph every unit test runs on (page 97, wave 0c).

200 accounts, 2,000 payments, two planted rings and one look-alike. Deterministic: run it again and the CSVs are
byte-identical. `uv run python tests/fixtures/make_fixture.py` rewrites them.

R1 is a mule ring: six new accounts on one device and one address, money in from outsiders, then a fast cycle.
R2 is structuring: eight new accounts sharing one email, each paying the collector just under 10,000.
L1 is a look-alike: a family of four on one laptop and one address, small payments between them.
"""
from __future__ import annotations

import csv
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "graph"
N_ACCOUNTS, N_PAYMENTS = 200, 2000
R1 = [f"A{i:04d}" for i in range(11, 17)]
R2 = [f"A{i:04d}" for i in range(101, 109)]
L1 = [f"A{i:04d}" for i in range(150, 154)]


def main() -> None:
    rnd = random.Random(7)
    OUT.mkdir(parents=True, exist_ok=True)
    accounts = [f"A{i:04d}" for i in range(1, N_ACCOUNTS + 1)]
    planted = set(R1) | set(R2)
    normal = [a for a in accounts if a not in planted and a not in L1]

    acc_rows, dev, eml, adr = [], {}, {}, {}
    for a in accounts:
        if a in planted:
            opened = date(2026, 9, 15) + timedelta(days=rnd.randint(0, 10))
        else:
            opened = date(2025, 1, 1) + timedelta(days=rnd.randint(0, 600))
        acc_rows.append({"account_id": a, "opened": opened.isoformat()})
        n = a[1:]
        dev[a], eml[a], adr[a] = f"D{n}", f"E{n}", f"AD{n}"
    for a in R1:
        dev[a], adr[a] = "D-R1", "AD-R1"
    for a in R2:
        eml[a] = "E-R2"
    for a in L1:
        dev[a], adr[a] = "D-FAM", "AD-FAM"

    tx: list[dict] = []
    t0 = datetime(2026, 6, 1, tzinfo=UTC)

    def pay(src: str, dst: str, amount: float, ts: datetime) -> None:
        tx.append({"tx_id": f"T{len(tx) + 1:05d}", "src": src, "dst": dst, "amount": round(amount, 2),
                   "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ")})

    # R1: money in from outsiders, then a cycle through the ring within hours, three times.
    for round_ in range(3):
        start = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=7 * round_)
        for k in range(4):
            pay(rnd.choice(normal), R1[0], rnd.uniform(4000, 4900), start + timedelta(minutes=10 * k))
        for i, a in enumerate(R1):
            pay(a, R1[(i + 1) % len(R1)], rnd.uniform(3800, 4700), start + timedelta(hours=1 + i))
    # R2: each member pays the collector just under 10,000, several times.
    collector = R2[-1]
    for k in range(4):
        for a in R2[:-1]:
            pay(a, collector, rnd.uniform(9500, 9990), datetime(2026, 10, 3 + k, 9, tzinfo=UTC)
                + timedelta(minutes=rnd.randint(0, 300)))
        pay(collector, rnd.choice(normal), rnd.uniform(30000, 60000), datetime(2026, 10, 3 + k, 18, tzinfo=UTC))
    # L1: a family moving small amounts between themselves, and paying outsiders.
    for _ in range(30):
        a, b = rnd.sample(L1, 2)
        pay(a, b, rnd.uniform(10, 150), t0 + timedelta(days=rnd.randint(0, 150), hours=rnd.randint(0, 23)))
    # Everyone else: ordinary payments.
    while len(tx) < N_PAYMENTS:
        a, b = rnd.sample(normal + L1, 2)
        pay(a, b, min(rnd.lognormvariate(4.5, 1.0), 5000),
            t0 + timedelta(days=rnd.randint(0, 150), hours=rnd.randint(0, 23), minutes=rnd.randint(0, 59)))

    def write(name: str, rows: list[dict]) -> None:
        with (OUT / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    write("accounts.csv", acc_rows)
    write("devices.csv", [{"device_id": d} for d in sorted(set(dev.values()))])
    write("emails.csv", [{"email_id": e} for e in sorted(set(eml.values()))])
    write("addresses.csv", [{"address_id": x} for x in sorted(set(adr.values()))])
    write("uses_device.csv", [{"account_id": a, "device_id": dev[a]} for a in accounts])
    write("has_email.csv", [{"account_id": a, "email_id": eml[a]} for a in accounts])
    write("has_address.csv", [{"account_id": a, "address_id": adr[a]} for a in accounts])
    write("tx.csv", tx)
    truth = [{"account_id": a, "ring_id": "R1" if a in R1 else "R2" if a in R2 else "",
              "lookalike_id": "L1" if a in L1 else "",
              "pattern": "mule_ring" if a in R1 else "structuring" if a in R2 else "family" if a in L1 else ""}
             for a in accounts]
    write("ground_truth.csv", truth)
    print(f"wrote {len(accounts)} accounts and {len(tx)} payments to {OUT}")


if __name__ == "__main__":
    main()
