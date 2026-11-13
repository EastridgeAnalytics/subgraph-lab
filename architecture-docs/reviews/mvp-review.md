# Wave 4 · the director's review of the MVP

13 November 2026. The whole codebase read against pages 00 to 08 and 88, as [97](../97-development-plan.md) says:
about 10,500 lines of Python in `lab/`, `examples/` and `generator/`, about 4,400 lines of TypeScript in `app/src/`,
and the tests. No builders ran in this wave. Each finding names a file, what happens, and the smallest fix; the
redesign items say what the new shape is. Wave 5 takes them as briefs.

**Verdict.** The loop works end to end on the seed, through the app and through an agent; the safety rules hold under
test (no push without confirm and a fresh review, no customer node created, nothing deleted); and A2 to A7 and A9 to
A11 pass as runnable tests. The app, built against a mock, worked against the real lab on its first walk. Five things
are worth changing before the measured page, in this order:

1. **Communities bury the rings (R6).** On the seed slice the largest communities hold 450 to 758 accounts; 45 of 60
   planted rings come out whole, but mostly whole inside groups that large, where their signal is diluted.
2. **The account score (R1).** It lowers every account when a factor is added, and recall is 0.36 at precision 1.00.
3. **The search slice is not selective (R2).** Two hops from 50 hits reached 6,546 of 10,000 accounts.
4. **The same jobs in two or three places (section 3)** and judgment numbers in code (section 4).
5. **Time to a first result (R5).** TGN took 966 seconds and the first pass 332 on the seed slice.

## 1. Pages against the code

| Page | What it says | What the code does | Fix |
|---|---|---|---|
| 02, 03, 04, 05, 06 | "Where it lives: ... Planned." | Built | Drop "Planned" |
| 03 | `to_pyg` gives node features, an edge index and timestamps | `edge_index` and `num_nodes`; no features, no timestamps | Say what it gives |
| 03 | `to_series` is a Cypher aggregation per account | A Polars aggregation over the payments, so `freq` stays free | Say so |
| 04 | The YAML example names `factors.velocity:score` and weights 0.30, 0.20, 0.50 | `lab.scoring.velocity:score`; the shipped weights differ | Show the shipped file's entries |
| 02 | `h` hops from each hit | Graph hops, so a shared device is two of them; see redesign R2 | Follows R2 |
| 97 | `examples/communities/` | `examples/networkx/` | Correct the name |

## 2. Acceptance tests A1 to A11

| Test | State |
|---|---|
| A1 · new user to a reviewed push in 15 minutes | Not yet: the walk script and its timing are wave 6. The first pass and TGN on the seed slice take minutes each, so the walk needs the small seed and a light recipe |
| A2 · every push follows a dry run the user saw | Passes (decision 37) |
| A3 · the same push twice changes nothing | Passes |
| A4 · `k` and `h` kept, estimate first | Passes |
| A5 · an agent's loop; apply needs confirm | Passes, and an agent ran it for real through both MCP servers |
| A6 · a failed batch resumes | Passes |
| A7 · the small seed fits Aura Free | The size check passes; loading into Aura waits for credentials |
| A8 · every measured number names its run | Not yet: the measured page is wave 6 |
| A9 · a planted number sends a summary to the template | Passes |
| A10 · a factor by YAML alone | Passes |
| A11 · ring recall | Passes on the fixture. On the seed slice (run `run-20261113T082716Z-52f819`): 45 of 60 rings whole, 48 mostly whole, no look-alike in a high-scoring community; but see R6 on community size |

## 3. The same job in more than one place

| Job | Where | Fix |
|---|---|---|
| Sending keys to Relate in batches | `lab/adapters/_read.key_batches` (used by seven modules) and `lab/roundtrip/delta._chunks` | One public `key_batches` in `lab/common/`; delta uses it |
| Finding payment cycles | `community_score._cycle_share` (its own search), `summaries/evidence.py` (`nx.simple_cycles`), `examples/networkx` (`nx.simple_cycles`), with two cycle-length constants | One function in `lab/scoring/`, one setting for the length; the example calls it |
| "Money in and out within a day" | The `in_and_out_within_a_day` factor (similar amounts, Cypher), the summary phrase and the community's `speed` part (any amounts, Python) | One definition, similar amounts, used by all three |
| Stamping `risk_run_id` and `risk_at` | `pipeline.stamp` and `stage._fill` | Stage only |
| Retrying a manifest save on Windows | `RunManifest.save` and `api/view.keep_saving` | `RunManifest.save` only |
| The newest run with a first pass | `api/ops.newest_with_first_pass` and `slice/estimate._search_run` | One function |
| Staging alerts | `attach_summaries` and `score` both stage them, so the manifest counts them twice | Stage once |
| Reading the run store's graph | `lab.adapters._read` is the shared reader for scoring and slicing, but private | Make it public |

## 4. Judgment numbers outside `settings.py` and the factor and policy files

| Number | Where | Fix |
|---|---|---|
| `MEMBER_RISK_SHARE` 0.5 | `scoring/community_score.py` | `community.member_risk_share` in the policy |
| `CYCLE_MAX_LENGTH` 6 and `MAX_CYCLE` 6 | `community_score.py`, `examples/networkx` | One setting, `LAB_CYCLE_MAX_LENGTH` |
| `HOLDOUT_DAYS` 14, `MIN_HISTORY_DAYS` 28, `MIN_ACTIVE_DAYS` 3 | `scoring/velocity.py` | Settings |
| `SMALL_GROUP` 8 | `summaries/template.py` (which innocent explanation a group gets) | A setting |
| `TOP_MEMBERS`, `MAX_IDENTIFIERS`, `MAX_EVIDENCE_IDS`, `CYCLE_COUNT_CAP` | `summaries/evidence.py` | Settings: they decide what a reviewer and a model see |

Operational constants stay in code: timeouts, page sizes, Relate's documented sizes, rank fusion's 60 (page 02 names
it), and the examples' own hyperparameters (examples are code a user edits).

## 5. Failures, logs, what leaves

- Every broad `except` either re-raises with a readable message, records the failure on the manifest, or is a
  shutdown path; none hides a failure. Logs carry keys and reasons, never evidence or summary text.
- Nothing calls a model without `ANTHROPIC_API_KEY` (tested). Evidence sent to a model holds keys and totals.
- `retract` leaves an account that holds only a disposition, because a disposition carries no run id. Small: stamp
  the run id on dispositions too.
- The summary cache keys on an evidence hash that includes the run id, so a new run never reuses a cached summary.
  Fix: hash the evidence without `run_id` and `key` for the cache.

## 6. Redesign

**R1 · The account score.** Today `risk_score` is a weighted mean in which a factor that does not fire counts 0. Adding
any factor lowers every account's score, and on the seed recall is 0.36: the structuring ring cannot reach the 0.6
line. New shape: a noisy-or of weighted factors, `1 - Π(1 - wᵢ·sᵢ)`, where each weight is how much that factor alone
can say. A factor that does not fire changes nothing, a new factor can only add, and two independent signals add up.
The policy keeps its threshold; the shipped weights and threshold are re-set for the demo data and the measured page
reports precision and recall.

**R2 · Hops that mean accounts.** Two graph hops from 50 hits reached 6,546 of 10,000 accounts on the seed: a hop
through a device or an email counts as one, so two hops is one account away by identifier plus one by payment. New
shape: `h` counts account-to-account steps, by a payment or by a shared identifier (each worth one), so `h` 1 is the
hits' direct counterparties and identifier-mates. Default `h` 1.

**R3 · Community parts that separate.** On the fixture, ordinary dense communities score 0.6 to 1.0 on `cycles` and
0.3 to 0.6 on `speed`. New shape: `cycles` counts time-respecting cycles (each payment after the one before it, within
the window), and `speed` uses the one "in and out within a day, similar amounts" definition from section 3.

**R4 · The app against the real lab.** Every screen was built and tested against the mock API. Wave 4 walked it
against the lab: see section 7. Plus the contract gaps the screen builders named: paging (`limit`, `offset`) in
`client.ts`; a route that returns a factor's source so Models can show it; choosing factors in a recipe.

**R5 · Time to the first result.** On the seed the first pass took 226 to 399 seconds, and TGN 966 seconds on the
loop's slice (4,100 accounts scored). For A1 the walk uses the small seed's slice and a recipe whose models are listed
on the Models screen; the measured page reports each step's time.

**R6 · Communities that keep rings apart.** The walk below opened the top community of a seed slice: 68 accounts,
score 0.15, holding a whole mule ring (six accounts on one device and one address, each scoring 0.65) merged by
Louvain with 62 ordinary accounts through payments. The ring's signal is diluted to nothing, which is the resolution
limit page 04 warns about. New shape: a first pass groups accounts joined by a shared identifier (each identifier
shared by at most `LAB_SHARED_MAX` accounts) into their own communities, and Louvain then runs on the payment graph of
what is left; or Louvain on the shared-identifier graph alone, with payments only as a score part. The measured page
reports ring recall either way (A11).

**R7 · The review's plan table.** On the walk the plan showed "Created 0" for every entity on a push that added 10
alerts and 118 communities: the dry run's JSON does not carry the planned creates the way the table expects. The
delta above it was right. Fix: fill the plan's created column from the delta's new keys, or drop the column and say
the delta is the comparison.

## 7. The app against the lab

Every screen was built and tested against the mock API. On 13 November the director walked the built app, served by
`python -m lab serve`, against the bundled Neo4j with the seed, in a browser (Playwright).

| Screen | What happened |
|---|---|
| Connect | Read the connection in use, tested it: Neo4j 2026.08.1, the Query API, the Relate pull route |
| Models | Read the real factor file and policy; unticking TGN changed the next run's models |
| Slice | A schema slice since 20 October estimated at 7,254 accounts, 28,677 nodes, 30,882 relationships, 41.4 MB, counted on the master graph; Run unlocked after the estimate |
| Run | Followed run `run-20261113T083732Z-4391d1` live: pull 18 s, first pass 2 min 48 s, scoring 55 s with 10 alerts, 118 communities and 30 template summaries, review 10 s |
| Results | Accounts and communities from the real staged results; a community opened to its graph, score parts, template summary, evidence and "communities like this one" |
| Review and push | The summary sentence, the delta (10 alerts, 118 communities, 7,254 scores, 1,000 of 1,000 sampled accounts found), the plan, the confirm dialog with the master graph's address; the push landed every entity and the `LabRun` in 47 s |
| Timeline | Re-queried the store as of 26 October and the totals followed |

From clicking Estimate to the landed push took about ten minutes on this slice, without TGN. Development timing,
not a measurement; A1 is measured in wave 6. The run was retracted afterwards.

Found on the walk, besides R6 and R7:

- The stage step counts communities and alerts twice (236 and 20 for 118 and 10): the double staging in section 3.
- The Run screen shows "Reused nodes 27,543" from `relate pull`'s summary line, which wave 1 found unreliable. Drop
  the reused counts from the step.
- At a 740-pixel width the nav wraps "Review and push" onto three lines. A narrower label or a wrap rule.
- The connection test logs a driver warning ("Expected a result with a single record, but found multiple"). Read
  the server info with a query that returns one row.
