# generator

The demo bank. Accounts with devices, emails and addresses as spokes, payments over time, and planted patterns
with a ring id on each: mule rings, structuring under a round threshold, rapid pass-through. Beside them, honest
look-alikes that should not score high: a family on one laptop, a payroll account paying many people, a landlord
collecting rent just under a round number. Page [07](../architecture-docs/07-what-runs-where.md) says where it
runs.

`python -m generator --size small|1m --out DIR [--seed N]` writes the CSVs that `import.sh` loads with
`neo4j-admin database import`, `ground_truth.parquet` with every planted account and ring, and `generator.json`.
`schema.cypher` adds the uniqueness constraints. `small` is 39,565 nodes and 150,000 relationships; `1m` is
276,987 nodes and 1,000,000 relationships. Compose runs it as the `generate`, `seed` and `seed-schema` services.

`neo4j-admin import` cannot reach Aura. `python -m generator.load [--size small] [--dir DIR]` writes the same graph
over Bolt to the database the `NEO4J_*` settings name, merging on each key. It only fills: it reads first and
refuses a database that holds anything but this seed, before writing anything (decision 52). With Compose:
`docker compose run --rm lab python -m generator.load`.

`model.py` makes the graph, `writer.py` writes it, `load.py` loads it over Bolt. Tests: `tests/test_generator.py`
checks the planted counts against the ground truth at both sizes with a fixed seed, and its live test loads the
seed both ways into a throwaway Neo4j and requires the same graph; `tests/test_generator_load.py` covers the
loader's statements and its guard.
