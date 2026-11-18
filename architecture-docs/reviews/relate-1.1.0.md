# Relate 1.1.0 against the lab

18 November 2026. Relate 1.0.2 came out on 16 November and 1.1.0 on 17 November, so a judge who installs Relate
today gets 1.1.0. The lab moved its pin from 1.0.1 to 1.1.0 (decision 49) after this check. The CLI came from the
release page, its SHA-256 matching `SHA256SUMS` (`relate-windows-amd64.exe` 796b78c8…, `relate-linux-amd64`
7c90a2ec…). The library is `relate-db==1.1.0` from PyPI. Every call ran with `DO_NOT_TRACK=1`.

**Verdict.** The lab runs on 1.1.0 with one fix. Relate's refusal of a commit over its size cap now says "64 MiB"
where 1.0.1 said "64 MB", and the loader recognised the refusal by "64 MB". A batch over the cap would have failed
instead of halving and retrying. The loader knows both wordings now, and its slow test makes the real Relate refuse
and recover. The default test run skips that test, which is why only a full check found it.

| Claim | Where | Result on 1.1.0 |
|---|---|---|
| `relate pull` copies whatever the Cypher returns over the Query API v2 | 01 | Holds. 46 nodes, 50 relationships and 4 constraints from the test Neo4j |
| The destination must be empty | 01 | Holds, same message: "destination database is not empty; pull writes into an empty database only" |
| `relate push` merges on keys, never deletes, a dry run first | 01 | Holds. The live tests push, fail on purpose, resume, and push the same run twice; the second push changes nothing (A3). The `--json` report reads as the ledger expects |
| Push carries every property type | 01 | **Still fails for zoned datetimes**, with HTTP 400 from Neo4j's Query API. The live test that uses it to make a batch fail passes. Decision 23 stands: the push store carries UTC strings |
| `relate serve --read-only=live` follows another writer | 00, 07 | Holds. The unit test that starts a real server sees the lab's write and refuses a write of its own |
| Relate's MCP server offers `get_schema` and `read_cypher`, read-only | 06 | Holds. `write_cypher` is absent; `read_cypher` takes `statement`, as on 1.0.1 |
| One commit cannot exceed 64 MiB | 01, 07 | Holds. 50,000 rows commit; 150,000 rows raise `DataError`. The text changed to "64 MiB" and the refusal now comes at the statement. The loader is fixed for the text |
| Vector and full-text indexes, hybrid `SEARCH` in one statement | 02 | Holds. The unit tests on the fixture store, and the four queries on `demo/README.md` |
| The committed demo store | demo | Opens on 1.1.0, and every command on `demo/README.md` returns its quoted output. A query that opens the file for writing touches 49 bytes of its header, so the page's queries now pass `--read-only` and the file stays as committed |
| Errors on the command line | all | 1.1.0 ends an error with a `hint:` line about `relate agent-feedback`. The lab reads the `Error:` line, and its fallback now skips the hint |

## The tests on 1.1.0

| What | Result |
|---|---|
| Unit tests, `uv run pytest -q` | 700 passed. The two that failed held the version as text; both now read the pin from `tools/install_relate.py` |
| The loader's slow test, a real refused commit | Passes with the fix. Before it, the check read 1.1.0's message as some other error |
| Live tests against the test Neo4j, `-m live` | 35 passed |
| The image | Builds on Debian 13 (the 1.1.0 binary still needs glibc 2.39). Inside it `relate --version` prints 1.1.0, `relate-db` is 1.1.0, and the licence check reads 93 distributions with nothing to look at |

## What 1.1.0 changes that the lab does not use yet

Keyed `MATCH` and `MERGE` no longer need a declared index. A `SEARCH` no longer lets a candidate its `WHERE`
rejects take a `LIMIT` slot. `ORDER BY … LIMIT` holds less memory. None of these is measured here, and 98's other
numbers were taken on 1.0.1.

## Worth reporting upstream

The zoned-datetime push failure from wave 0 is still there on 1.1.0. Relate's repo takes issues, not pull
requests; filing one with the wave-0 reproduction is the owner's call.
