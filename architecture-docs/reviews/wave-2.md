# Wave 2 · find and score

13 November 2026. Five tracks, three builders at a time, as [97](../97-development-plan.md) says.

**Verdict.** Done. The loop runs on the seed with the first pass, a search-defined slice and real scores, and an
agent ran it end to end through the two MCP servers. The default suite is 404 tests, the live suite 30, the app 98;
all green on `main`.

## What landed

| Track | What | Where |
|---|---|---|
| 2a | The first pass (Node2Vec from PyG's example, template account summaries), the search-defined slice (hybrid search with rank fusion, then hops, size and memory first, the cap), the pull estimate | `lab/slice/`, `examples/node2vec/` |
| 2b | Five Cypher rule factors, the factor runner (cypher, python, model), the policy, account scores and alerts, `score(run)` | `factors/`, `lab/scoring/factors.py`, `policy.py`, `pipeline.py` |
| 2c | PyG's TGN example, single-threaded and seeded, as the `tgn` factor | `examples/tgn/` |
| 2d | 19 routes, the lab's MCP tools at `/mcp`, the run worker, `relate serve --read-only=live` supervised on the current run, the app at `/` | `lab/api/` |
| 2e | The Run and Review and push screens | `app/src/screens/` |
| Director | The pipeline: a recipe step by step up to the review, resumable | `lab/pipeline.py` |

## The loop on the seed

Run `run-20261113T065827Z-536fc3`, payments since 15 August 2026. Development timings on this laptop, with three
builders sharing the CPU; not measurements for the docs.

| Step | Result |
|---|---|
| Pull | Relate route, 39,293 nodes, 90,605 relationships |
| First pass | Embeddings and summaries on 9,932 accounts |
| Search | 50 hits for "money in and out within a day across new accounts", 25,870 nodes within 2 hops |
| Score | 6,546 accounts, 97 alerts, 5 factors ran, 2 skipped (TGN and velocity were not in this loop) |
| Push and retract | `LabRun` landed with 6,546 scores and their embeddings; retract left the demo graph clean |

Checked against the generator's ground truth for the seed (an earlier run on the same data, run
`run-20261113T052505Z-b28055`): 5 of the search's top 10 hits and 25 of its top 50 are planted accounts, where 3.7%
of accounts are planted. Alerts: precision 1.00, recall 0.36 of the planted accounts in the slice, no look-alike
alerted. The recall goes to the wave 4 review.

## The agent run

Claude Code (Sonnet), headless, with only the lab's MCP server and Relate's read-only MCP server. Run
`run-20261113T074745Z-b4a987`:

- `egress` with a search recipe, `run_status` until the first pass was done, `search_slice` (19 hits, 247 accounts
  in reach), `score`, `get_schema` and `read_cypher` on Relate's server, `preview_push`, `apply_push` with confirm.
- The push landed 247 account scores, 23 alerts and their links, and the `LabRun`. Retracted afterwards.
- What it could not do came from wave 3 not being merged: no communities, no summaries, no evidence to explain.

The first attempt failed in a useful way. `egress` ran the pull and the first pass inside one tool call, longer
than the agent's tool timeout; the agent gave up twice and left two half-done runs. **Decision 38**: the long tools
queue their work and answer at once, and a ninth tool, `run_status`, waits server-side up to 240 seconds.

## Decisions

37 (a push from the API or an agent needs a review newer than the last thing staged) and 38, on
[08](../08-decisions.md).

## Fixes the director made while merging

- Relate connections belong to the thread that opened them (DB-API threadsafety 1), and the API runs requests on
  several threads. `Run` keeps one connection per thread and store.
- `RunManifest.save` retries the replace while a reader holds the file on Windows.
- A run that reached its review reads as done; the Run screen stopped polling forever.
- Scoring stages each account's embedding when the recipe writes vectors back.
- The summary phrases' thresholds became settings; a pull estimate is never refused for the search cap.
- `LAB_APP_DIST` became a setting.

## How the wave ran

Three builders at a time; no usage-limit error. Wave 3's tracks 3a to 3e started while wave 2 closed, because their
inputs were merged; none merged before this report.

## Open, carried forward

- Alert recall on the seed, and the policy's habit of lowering every score when a factor is added (wave 4).
- The first pass on the seed took 226 to 399 seconds; TGN trains at about 2.4 seconds per epoch per 2,000 payments.
  Both are to measure in wave 6, and to weigh against the 15-minute walk (A1).
- The search returned 19 hits where the manifest says 20; to look at in wave 4.
