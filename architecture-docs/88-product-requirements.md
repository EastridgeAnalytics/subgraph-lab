# 88 · Product requirements, MVP 1.0

Version 1.0, 12 November 2026. Owner: Tim Eastridge. Status: draft for go or no-go.

## Purpose

subgraph-lab lets a Neo4j team pull a subgraph from Aura into Relate, find the part that matters with hybrid
search, score its accounts and communities, summarise why a community could be risky, and push the results back
after a reviewed dry run. MVP 1.0 is the Relate by Neo4j hackathon submission, due 11 December 2026.

## Users and jobs

| User | Job |
|---|---|
| Data scientist | Run a PyG or TGB model on a Neo4j subgraph without writing export or load code |
| Fraud reviewer | Ranked accounts and communities with a score, the reasons and a summary they can check, and a look at what will change before it lands |
| Agent | Tools that read and propose, with a human gate on every write |

## Functional requirements

**Connect.** R1 Aura from `.env` or the Connect screen, or the bundled Neo4j with no setup. R2 Test the
connection and name the pull route that will run.

**Pull.** R3 `relate pull` with a Cypher query into a fresh store per run. R4 Parallel Bolt reads when the Query
API is not reachable, with the route recorded. R5 Recreate the vector and full-text indexes after every pull.

**Find.** R6 Slice by labels, types and time window, by Cypher, or by search. R7 Search runs vector and full-text
in one statement, fuses by rank, keeps the top `k` (default 50) and expands `h` account-to-account steps (default 1). R8 Show hits,
size and memory before any model runs, and refuse past a cap.

**Enrich.** R9 Adapters to an Arrow table, PyG `Data`, PyG `TemporalData` in the TGB layout, and NetworkX. R10
Examples under 100 lines, each ending in stage and push: TGN, Node2Vec, NetworkX communities and cycles, the
statsforecast velocity factor. R11 Embeddings stored with a vector index in Relate.

**Score.** R12 Risk factors in a versioned YAML file checked against a schema: Cypher, Python or a model, each
with a threshold and a weight. A new entry needs no code change. R13 A policy file combines factors into an
account score, with each part on the Account. R14 Communities per slice: connected components, then seeded
Louvain on weighted payment and shared-identifier links, keyed deterministically per run. R15 A community score
from member scores and the group's shape, every part visible.

**Summaries.** R16 One evidence object per alert and community, checked against a JSON schema and hashed. R17
The top 20 communities and top 50 alerts get 120 words or less: what the evidence shows, why it could mean risk,
and the innocent explanation to rule out. The Anthropic API writes them if a key is set, else the agent, else a
template. R18 Every number must be in the evidence, every account named must be a member, and no verdict words.
Any failure keeps the template. Summaries are stored and cached with their evidence hash, model and source.

**Review and push.** R19 Results staged in Relate under a run id, in `risk_*` properties. R20 Review shows the
dry-run plan plus the delta: new alerts and communities, score changes, keys missing remotely, vectors, bytes.
R21 One confirm. `apply_push(confirm=true)` is the lab's only code path that writes to the master graph. R22 Reviewers
mark accounts and communities confirmed, false positive or needs more, written back as `risk_disposition` with
who and when. R23 `relate push`: keyed merge, batches of 5,000, no deletes, creating `RiskCommunity` nodes and
member links. R24 A manifest per batch, so a stopped push resumes. R25 A `LabRun` node written last, with counts,
model hash and factor file version. R26 Bolt fallback for retractions and for servers without the Query API.

**App, agent, packaging.** R27 Seven screens: Connect, Slice, Models, Run, Results, Review and push, Timeline.
React, Vite, TypeScript and Tailwind on FastAPI. R28 Defaults everywhere, estimates before anything slow, "show
code" on every screen. R29 Nine lab MCP tools; the long ones queue and answer at once, and `run_status` follows them. `explain_alert` and `describe_alert` take an account or community
id. R30 The agent reads through Relate's MCP server, read-only, with Relate's skill. R31 One `lab` image (CPU
default, GPU profile), Relate serving read-only beside it, an optional bundled Neo4j with the seed,
`DO_NOT_TRACK=1`.

## Non-functional requirements

Runs on an 8 GB laptop with the small seed. Nothing leaves the container unless Aura or model credentials are
set, and evidence sent to a model carries keys and totals, never names. Never deletes anything on the master
graph. The same inputs and seed give the same communities and scores. MIT licence. AI use described in the
submission.

## Acceptance tests

| Test | Group | Passes when |
|---|---|---|
| A1 | Use | Someone new goes from `docker compose up` to a reviewed push on the seed in 15 minutes or less |
| A2 | Safety | Every push follows a dry run the user saw |
| A3 | Safety | Pushing the same run twice changes nothing the second time |
| A4 | Use | A search slice keeps to `k` hits and `h` hops, with the estimate shown first |
| A5 | Safety | An agent runs pull, search, score, preview and apply through MCP, and apply fails without confirm |
| A6 | Safety | A push with a forced failing batch resumes and finishes |
| A7 | Measurement | The small seed loads into Aura Free |
| A8 | Measurement | Every number on the measured page names its run |
| A9 | Safety | A summary with a planted number that is not in its evidence falls back to the template |
| A10 | Use | A factor added to the YAML file shows up in scores and summaries on the next run, with no code change |
| A11 | Measurement | The measured page reports how many planted rings came out as one community, and how many look-alikes scored high |

## Out of scope for 1.0

The Markdown review pack, the single-file run, the in-browser timeline, a local language model, Arrow Flight,
any model code of our own, retraining on dispositions, suppressions, whole-graph communities, 10M and 50M runs,
history and compare, the PyG remote backend, Aura Graph Analytics write-back, sign-in and roles.

## Open questions

TGB's licence, pull and push speed against Aura, live reads beside the writer, Aura Free's real limit, pushed
vectors in Neo4j's index, and summary cost. The first factor list, weights and summary wording are the project
owner's call.

## Success

By 11 December, a complete submission that passes A1 to A11 and speaks to the five judging criteria as scored:
Relate and connected context 30 points, functionality and completeness 25, usefulness 20, documentation and
reproducibility 15, creativity 10. By the end of January, one Neo4j team outside Eastridge Analytics runs a recipe
on its own Aura.
