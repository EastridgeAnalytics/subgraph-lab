# Wave 0 · Relate 1.0.1 against the pages

12 November 2026. Every Relate claim on pages 00 to 08 run against Relate v1.0.1 (released that day) and a local
`neo4j:2026.08.1` community instance. The CLI came from the release page, its SHA-256 matching `SHA256SUMS`
(`relate-windows-amd64.exe` 08f3e1ba…, `relate-linux-amd64` 2ca69bd6…). The library is `relate-db==1.0.1` from
PyPI. Every call ran with `DO_NOT_TRACK=1`; `relate telemetry status` says `disabled (DO_NOT_TRACK)`.

**Verdict.** The round trip works as the pages describe, with three differences that change the design: push
fails on zoned datetimes, push must run from a separate store, and no Relate server image is published. Each is a
decision on [08](../08-decisions.md).

| Claim | Page | Result |
|---|---|---|
| `relate pull` copies whatever the Cypher returns over the HTTP Query API v2 | 01 | Holds. 50 nodes and 49 relationships pulled; the uniqueness constraint came too |
| The destination must be empty | 01 | Holds. A second pull into the same store: "destination database is not empty; pull writes into an empty database only" |
| Pull skips vector and full-text indexes | 01 | Not exercised: the source had none. Pull reports "0 schema skipped". The lab creates both indexes after every pull either way |
| Credentials come from `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | 01, 07 | Holds, from `--from FILE` or the environment. `NEO4J_URI` must be `http(s)://`; a Bolt URI maps only on the default port (7687 to 7474) and fails otherwise. A non-loopback `http://` needs `--allow-plaintext` |
| `relate push` merges on keys in batches of 5,000 and creates nodes | 01 | Holds. Keys come from the store's uniqueness constraints or `--merge-key`; a label with neither is skipped and reported, never guessed |
| Push never deletes; a failing batch stops the push with a report | 01 | Holds. The report names the batch, the row range and the statement |
| The dry run prints the plan without contacting the remote | 01 | Holds. Its "created" column is the plan, not a diff against the remote |
| Push carries vectors | 01, 02 | A list of floats lands as `LIST<FLOAT>` |
| Push carries every property type | 01 | **Fails for zoned datetimes.** `datetime(...)` at any precision returns HTTP 400 from Neo4j's Query API. `date`, `localdatetime`, `time`, `duration`, lists and booleans land. Decision 23 |
| Pushing the pulled graph back is safe | 01 | **No.** It re-sends every pulled property, and relationships with no key merge one per type per pair, so parallel payments between two accounts would collapse. Decision 22 |
| `relate serve --read-only=live` follows another writer | 00, 07, 08 | Holds. A commit from another process was visible within two seconds; writes fail with `WriteOnReadOnlyAccessDatabase` |
| Relate's MCP server offers `get_schema`, `read_cypher` | 06 | Holds. `write_cypher` is absent in read-only mode |
| Relate's own image runs `relate serve` | 07 | **No published image found** on ghcr.io or Docker Hub; the README names `relate-bolt:latest`, built locally from the repo. Decision 21 |
| The Python library is on PyPI | 01 | Holds since 1.0.1 (the 0.1.x line was releases-only) |
| `execute_arrow()` returns Arrow | 00, 03 | `relate.Database.execute_arrow()` exists; `relate.connect()` returns a DB-API connection whose cursor has `to_arrow()`, `to_df()`, `data()` and `graph()`. The lab uses the cursor. Decision 27 |
| One commit cannot exceed 64 MiB | 01, 07 | Holds. 50,000 rows (about 21 MB) commit; 150,000 rows (about 63 MB) raise `DataError` naming the 64 MB per-record cap |
| Vector and full-text indexes, and `SEARCH` | 02 | Hold. `CREATE VECTOR INDEX … OPTIONS {indexConfig: {…}}` and `CREATE FULLTEXT INDEX … ON EACH [...]` work |
| Hybrid search with rank fusion in one statement | 02 | Holds, with each branch ranking its hits by `collect` then `UNWIND range(...)`. The query on 02 now shows that form |
| `relate skill install` | 06 | The command exists (`install`, `remove`, `list`, `check`, `--agent NAME`). Not run, so no agent's skills folder on this machine was changed |

## What else wave 0 found

- **Python 3.12, not 3.11.** NetworkX 3.7 requires Python 3.12 or later. Decision 18.
- **PyTorch 2.12 and pyg-lib 0.7.0.** PyG's Node2Vec needs pyg-lib 0.6 or later; 2.12 is the newest PyTorch with
  pyg-lib CPU wheels for both Windows and Linux. Decision 19.
- **Ports.** This machine already runs two other Neo4j instances on 7474, 7687, 17474 and 17687. Every host port
  is a setting with the documented default. Decision 26.
- **Neo4j image.** `neo4j:2026.08.1` community serves the Query API v2 at `/db/{name}/query/v2`. Decision 20.

## Worth reporting upstream

The zoned-datetime push failure is a Relate bug or a Query API mismatch. The repo takes no outside pull requests;
an issue with the reproduction above is the owner's call.
