# 99 · Build status

**Nothing is built as of 12 November 2026.** The design is complete. This page changes in the same commit as any
piece that lands.

## The MVP, by 11 December 2026

| # | Deliverable | Done means | Track | Status |
|---|---|---|---|---|
| 1 | Contracts | The run manifest, the staged-results table, the recipe file, the REST routes, the MCP tool list and the lint config, written and reviewed on day one | Integrator | Not started |
| 2 | Generator and seed | Accounts with devices, emails and addresses. Three planted patterns with ring ids, plus legitimate look-alikes. A small seed that fits Aura Free's smaller published limit, and 1M relationships for the measured page | Data | Not started |
| 3 | Pull | `relate pull` from Aura into a fresh store per run. Parallel Bolt as the fallback. Both measured at 100k and 1M relationships | Data plane | Not started |
| 4 | Indexes and first pass | Node2Vec embeddings and account summaries, with vector and full-text indexes created in Relate | Adapters | Not started |
| 5 | Search-defined slice | Hybrid search plus hops, the size and memory shown before expansion, the cap enforced | Data plane | Not started |
| 6 | Adapters | Table, PyG graph, PyG event stream in the TGB layout, NetworkX. Each with a test on the seed | Adapters | Not started |
| 7 | Examples | TGN scores, Node2Vec similar accounts, NetworkX communities and cycles, the statsforecast velocity factor. Each under 100 lines, each ending in stage and push | Adapters | Not started |
| 8 | Risk scores and communities | The factor file with five rule factors, the policy that makes account scores, communities found per slice, the community score, ring recall against the planted rings | Scoring | Not started |
| 9 | Summaries | The evidence schema and hash. Community and account summaries through the Anthropic API or the agent, the template otherwise. The number, member and wording checks. The cache | Scoring | Not started |
| 10 | Review and push | The dry-run plan plus the delta. `relate push` keyed, with a manifest and resume, creating `RiskCommunity` nodes and their links. Dispositions written back. The `LabRun` marker written last. Bolt as the fallback | Data plane | Not started |
| 11 | API and agent | FastAPI routes, the eight tools, Relate's skill and MCP wired for the agent | API | Not started |
| 12 | The app | The seven MVP screens, with accounts and communities on Results and the decision buttons. The 15-minute test, measured | App | Not started |
| 13 | Compose, README, numbers, video | An `.env` for Aura and one command for the bundled Neo4j. AI use disclosed. A measured-numbers page. A video of two minutes or less | Docs | Not started |

## Four weeks

| Week | In parallel | Working at the end of the week |
|---|---|---|
| 1, from 13 November | Contracts. Generator and seed. `relate pull` from Aura. The table and PyG adapters. The app with Connect and Slice | A slice pulled from Aura and visible in the app |
| 2 | Indexes and first pass. Search-defined slice. TGN and Node2Vec examples. The factor file and account scores. Review and push. MCP tools | Search a slice, score it, push to Aura after a dry run |
| 3, Thanksgiving week | Communities and the community score. The evidence schema and summaries. The velocity factor. Models, Results and Review screens. Timeline slider | The full loop, end to end |
| 4 | Measured numbers. README. Video. Fixes only after 7 December | Freeze 9 December, submit 11 December |

## How the work runs

Agents build the tracks in parallel, each in its own worktree and folder, against the contracts from row 1. One
person integrates, merges daily, and runs the whole loop on every merge.

- **Nothing merges without its test on the seed.**
- **Every track owns its folder.**
- **Only the integrator changes a contract, with a note in the repo.**
- The submission describes how the agents were used.

## Out of scope for the MVP, on purpose

The Markdown review pack, the single-file run, the in-browser timeline, a local language model, Arrow Flight, any
model code of our own, retraining on dispositions, suppressions, whole-graph communities, 10M and 50M runs,
history and compare, the PyG remote backend, Aura Graph Analytics write-back, sign-in and roles.

## After the hackathon

The shape, not a commitment.

| Release | What's in it |
|---|---|
| 1.1, January 2027 | A review pack in Relate's Markdown codec: one note per alert, linked to its accounts, readable in Obsidian and diffable in git. Each run saved as one portable Relate file. The in-browser timeline on Relate's WebAssembly build. A local language-model profile for summaries. Whole-graph communities with GDS Leiden on Aura Graph Analytics, scored the same way. Arrow Flight on Aura Graph Analytics and AuraDS. The DGL adapter. History and compare. 10M relationships measured |
| 1.2, February to March 2027 | The full-population sweep: slices by key range or time window with a one-hop ring, a scheduler, resume. Retraining on reviewer decisions, and suppressions suggested by false positives. Re-scoring only the communities new events touch. Relate as a PyG remote backend. GNNExplainer and Captum adapters. Recipes on a schedule. Sign-in and roles |
| 2.0, second quarter 2027 | Model packs per use case (financial crime, churn, supply chain, identity), each a recipe, a factor file, an example and a review checklist. A run registry across instances. Audit export. GDS write-back parity. 50M relationships measured |

## What we won't claim until measured

- Any rows per second, megabytes per second or wall time for pull, push or load.
- How long a search-defined slice takes, or how big two hops get.
- "Faster than" anything.
- Relate's running memory for our data.
- Model accuracy on real fraud. The demo is synthetic.
- How well communities match real rings, or how good the summaries are, outside the demo data.
- Summary cost per community.
- How long a new developer takes to get through the app.

## Hackathon rules we keep

Started on or after 11 November 2026. Public under MIT at submission. AI use described. A video of two minutes or
less. "Built with Relate by Neo4j" is fine; a logo or an implied endorsement is not.
