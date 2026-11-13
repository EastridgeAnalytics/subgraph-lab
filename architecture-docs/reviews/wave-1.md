# Wave 1 · the data and the way in

13 November 2026. Five tracks, three builders at a time, as [97](../97-development-plan.md) says.

**Verdict.** Done. The loop runs on the bundled seed from pull to a verified `LabRun` and back to a clean graph:
`tests/acceptance/loop.py` against run `run-20261113T051338Z-b0b716`. The default suite is 215 tests, the live
suite 17, the app 47; all green on `main`.

## What landed

| Track | What | Where |
|---|---|---|
| 1a | The generator: mule rings, structuring, pass-through chains, and family, payroll and landlord look-alikes, with ground truth. `small` is 39,565 nodes and 150,000 relationships. The seed and its constraints in the bundled Neo4j | `generator/`, `compose.yaml` (`generate`, `seed`, `seed-schema`) |
| 1b | `relate pull` for schema and Cypher recipes; the Bolt fallback over parallel key ranges, recorded with its reason; the auto-sizing loader; the account constraint and four search indexes after every pull | `lab/roundtrip/pull.py`, `queries.py`, `loader.py`, `indexes.py` |
| 1c | The adapters: table, PyG, the TGB layout, NetworkX for communities and raw, Nixtla series | `lab/adapters/` |
| 1d | The app shell, the UI kit, Show code, the run context, Connect, Slice | `app/src/` |
| 1e | Staging into the push store, the review (dry run plus delta), apply with confirm, resume, `LabRun` last, the Bolt fallback, retract | `lab/roundtrip/stage.py`, `push.py`, `delta.py`, `ledger.py`, `retract.py` |

## The loop on the seed

One run, pulling payments since 15 August 2026 from the bundled Neo4j. Development timings on this laptop, not
measurements for the docs; wave 6 measures.

| Step | Result |
|---|---|
| Pull | Relate route, 39,293 nodes and 90,605 relationships |
| Stage | A hand-made table, 100 accounts (wave 2 replaces it with scores) |
| Review | The plan: Account 100. The delta: 100 new scores, 0 missing keys. The master graph unchanged |
| Push | Confirmed; `LabRun` on the master graph; 100 accounts carry the run's scores |
| Retract | The run's results gone; the demo graph clean |

## What Relate 1.0.1 did that the pages did not say

- **`IN $keys` slows with the square of the list.** 500 keys took 0.02 s, 5,000 took 4.6 s (track 1c, scratch store).
  Every reader sends keys `LAB_KEY_BATCH` at a time. Worth reporting upstream; the owner's call.
- **`relate push` fails with "storage locked" while the lab's writer is open on the push store.** The lab closes its
  stores before every `relate` call.
- **A failed `relate push --json` still prints the JSON report**, exit 1, with the cause on stderr. An entity that did
  not finish carries the plan's numbers, so the ledger zeroes them.
- **`--labels`, `--types` and `--query` are exclusive**, so a resume is up to two calls before `LabRun`.
- **A failed `relate pull` leaves a directory a second pull accepts as empty**; the lab removes it anyway.
- **`--param` values are JSON first, then raw strings**, so the lab encodes them with `json.dumps`.

## Decisions

30 to 35 on [08](../08-decisions.md). The safety one: **a push never creates a customer node** (35). Track 1e found
that a keyed merge creates an account the master graph lacks; `apply` now checks every staged account key over Bolt
first and refuses the whole push on any miss, with a live test.

## How the wave ran

- Three builders at a time. No usage-limit error was hit.
- Live tests moved to their own in-memory `neo4j-test` service, so the bundled Neo4j keeps the seed.
- Three judgment numbers a builder left in code became settings: `LAB_KEY_BATCH`, `LAB_WEIGHT_PHONE`,
  `LAB_WEIGHT_OTHER`.
- Wave 2's tracks 2a, 2b and 2e started while 1e finished, because their inputs were merged. None merged before this
  report.

## Open, carried into wave 2

- 2a's summary thresholds (60 days, 24 hours, 10,000 and 5%) and Node2Vec's numbers become settings at its merge.
- 2b's alert threshold lets the structuring ring score under the line on the fixture, and a new factor lowers every
  score because a factor that does not fire counts 0. Both go to the wave 4 review with the seed's numbers.
- Pull and push against Aura, and every number at 100k and 1M relationships, wait for wave 6.
