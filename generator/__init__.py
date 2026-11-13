"""The synthetic payments generator and the bundled-Neo4j seed (page 07, wave 1a).

`python -m generator --size small|1m --out DIR [--seed N]` writes neo4j-admin import CSVs, `ground_truth.parquet`
and `generator.json`. Bump `GENERATOR_VERSION` whenever the same seed would write different files.
"""

GENERATOR_VERSION = "1.0.0"
