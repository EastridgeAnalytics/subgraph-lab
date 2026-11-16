# tests

| Where | Runs on | Command |
|---|---|---|
| `test_*.py` | The fixture graph in `fixtures/`: 200 accounts, 2,000 payments, a mule ring, a structuring ring and one look-alike family. Deterministic | `uv run pytest -q` |
| `-m live` | A throwaway Neo4j, `neo4j-test` in compose, which the tests wipe. Never the bundled demo Neo4j | `docker compose --profile test up -d neo4j-test`, then `uv run pytest -q -m live` |
| `acceptance/` | A1 to A11 from [88](../architecture-docs/88-product-requirements.md) | See below |

In `acceptance/`: `test_acceptance.py` runs the tests that fit pytest; `a1_walk.py` walks a clean clone from
`docker compose up` to a reviewed push with Chromium and times it; `check_measured.py` is A8, every number on
[98](../architecture-docs/98-measured.md) has its record in `measured/`; `loop.py` runs the whole loop from compose
up to a verified `LabRun` in Neo4j, and ran after every merge.

`fixtures/make_fixture.py` rewrites the fixture. Run it again and the CSVs are byte-identical.
