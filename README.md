# subgraph-lab

Pull a subgraph from Neo4j into Relate, let open-source graph models learn from it, review the dry run, push the
results back. Built with Relate by Neo4j.

![Planted mule ring R0006 as the lab found it: five accounts on one device and one address, paying each other in loops](demo/img/graph.png)

**[The walkthrough](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/demo/README.md)**, one planted
ring from the graph to a reviewed push, with screenshots from one real run · **[The agent](demo/agent.md)**, Claude Code doing the same job through two MCP servers, from
its own log · **[Inspect the store](demo/README.md#inspect-it-yourself)**, five minutes with the `relate` CLI ·
**[In plain English](https://eastridgeanalytics.github.io/subgraph-lab/about.html)**, what Relate is, why we built
on it, and the fraud use case on top · **[How to get started](#how-to-get-started)**, for people and for agents.

## What you will see

The demo bank is synthetic: 10,000 accounts with their devices, emails and addresses, 120,000 payments, 60 planted
fraud rings and 60 honest look-alike groups, with the answer key written down.

Ring R0006 comes out as the top community of 172, at 0.84, and it is exactly the ring's five members. Its summary
names the innocent explanation to rule out first: a household, or a shared computer. Across the slice,
[against the answer key](demo/README.md#how-the-whole-slice-did), all 39 communities scoring 0.6 or more hold
planted ring accounts, none of the 60 look-alike groups scores that high, and 210 of the 211 alerts are on planted
ring accounts. The data is built to be found, so these numbers show the whole loop working, no more.

## Connected context

The models never see a table of accounts. They see the accounts with the devices, emails and addresses they share
and the money between them, pulled from Neo4j as one subgraph into a Relate store. Communities form from those
links, the score's parts are those links with the arithmetic shown, and the summary is written only from a checked
evidence object of them, so every number in it is in the graph.

## Why it matters

- **For a Neo4j team.** Value added on top of the graph they already have, a slice at a time, pushed back to Aura
  with a record of what changed.
- **For whoever answers for the data.** The dry run and the delta first, then one confirm. Every write carries a
  run id, nothing is deleted, and a run can be retracted.
- **For an agent.** It can read everything and change nothing without the gate.

## Fraud today, other verticals next

We build graph fraud detection on Neo4j for a living. Tim Eastridge, who leads Eastridge Analytics, is a Neo4j
certified expert, a Neo4j Ninja, and the author of *Graph Data Science with Python and Neo4j*. The team has five
certified Neo4j experts. The largest graph we run is fraud detection and entity resolution for federal
pandemic-spending oversight (PRAC), at 1 billion nodes and 2 billion relationships. The factors, the community
score and the evidence-first summaries come from that work. No client's code, schema or name is in this repo.

Nothing in the loop is specific to fraud except `factors.yaml` and the generator. Next is a pack per use case:
financial crime, churn, supply chain and identity, on the 2.0 row of [99](architecture-docs/99-build-status.md).
Same pull, same review, same push.

## How it uses Relate

- **Mode.** Directory: a fresh store for each run. The demo run is also one file, made by `relate clone`.
- **Why this mode.** `relate pull` fills an empty store, so each run starts clean, and the lab writes it while the
  app and the agent read it through `relate serve --read-only=live`. One file is what goes in a repo.
- **What the graph models.** Accounts, the devices, emails and addresses they share, and the payments between them;
  after a run, also the scores, alerts, communities and the `LabRun` that wrote them.
- **Where the data lives.** [`demo/store.rl`](demo/README.md#inspect-it-yourself) in this repo; your own runs in
  `data/runs/<run id>/store`.

| Relate feature | What the lab does with it | Where |
|---|---|---|
| `relate pull` | The slice into the run's store, over the Query API. Parallel Bolt is the fallback; the manifest records the route | `lab/roundtrip/pull.py` |
| `relate-db` in Python | Opens the store; loads results in `UNWIND` batches that size themselves | `lab/common/store.py`, `lab/roundtrip/loader.py` |
| Vector and full-text indexes | Recreated after every pull, on Node2Vec embeddings and short summaries | `lab/roundtrip/indexes.py` |
| Hybrid search in one statement | A vector branch and a full-text branch fused by rank, then hops. That subgraph is the slice | `lab/slice/search.py` |
| `relate serve --read-only=live` | The app and the agent read the store while the lab writes it | `lab/api/relate_server.py` |
| `relate push --dry-run`, then `relate push` | The plan before the write. Keyed merges in batches, a manifest that resumes, `LabRun` last | `lab/roundtrip/push.py` |
| The skill and the MCP server | `relate skill install`; the agent reads the evidence through `read_cypher` | `lab/api/mcp_tools.py` |
| `relate clone` | The demo run's store, committed as one `.rl` file | `demo/store.rl` |

Every row was checked against Relate 1.0.1 in [wave 0](architecture-docs/reviews/wave-0-relate-1.0.1.md) and again on
[1.1.0](architecture-docs/reviews/relate-1.1.0.md), the version the lab pins.

## How the pieces fit

| Step | Code | Page |
|---|---|---|
| The demo bank, with planted rings | `generator/` | [07](architecture-docs/07-what-runs-where.md) |
| The round trip: pull, stage, dry run, delta, push, retract | `lab/roundtrip/` | [01](architecture-docs/01-the-round-trip.md) |
| First pass and the slice: embeddings, summaries, indexes, search | `lab/slice/`, `lab/roundtrip/indexes.py` | [02](architecture-docs/02-the-slice.md) |
| Adapters and examples: table, PyG, TGB events, NetworkX, series | `lab/adapters/`, `examples/` | [03](architecture-docs/03-models-and-adapters.md) |
| Factors, policy, account and community scores, alerts | `factors/`, `lab/scoring/` | [04](architecture-docs/04-risk-scores.md) |
| Evidence and summaries | `lab/summaries/` | [05](architecture-docs/05-summaries.md) |
| The API, the nine tools and the seven screens | `lab/api/`, `app/src/screens/` | [06](architecture-docs/06-the-app-and-the-agent.md) |
| One run, pull to push | `lab/pipeline.py` | [00](architecture-docs/00-overview.md) |

Tests are in `tests/`, one file per piece; each code folder has a README.

## AI in the product, and AI that built it

**In the product.** An agent connects to two MCP servers: Relate's, serving the run's store read-only, and the
lab's, with nine tools from `egress` to `apply_push`. `apply_push` fails without `confirm=true` and without a
review newer than the last thing staged. Summaries come from Claude through the Anthropic API, from the agent, or
from a template, all through the same checks: every number in the text is in the evidence, every member named is a
member, no verdict words.

**In the build.** One Claude Opus session directed it from [the development plan](architecture-docs/97-development-plan.md).
Claude Sonnet builders took one written brief each, in their own git worktrees, up to three at a time, and nothing
merged without its test on the seed. Each wave's report is in [`architecture-docs/reviews/`](architecture-docs/reviews/).
One person owns the project and reviews it.

## What is measured, and what waits

702 unit tests on a fixture graph, 35 live tests against Neo4j, 254 app tests, and A1 to A11 from
[the requirements](architecture-docs/88-product-requirements.md) in [`tests/acceptance/`](tests/acceptance/).
Every number in the docs is on [98](architecture-docs/98-measured.md) with the run that produced it: a pull of one
million relationships takes 124 seconds by `relate pull`, and a clean clone to a reviewed push takes 160 seconds
after the first image build. Waiting: the Aura rows on 98 and test A7, which need a run on an Aura instance;
summaries by API, which need a key; the video.

## How to get started

**Before you start.** Numbers are from [98](architecture-docs/98-measured.md).

| What | Needs |
|---|---|
| Docker | Docker running, with Compose. Built and tested on Docker 29 with Compose 5 (Docker Desktop on Windows 11), and built in CI on Linux |
| Disk | 4.66 GB for the lab's image once built, plus Neo4j's own image for the bundled graph |
| Memory | On the 1M graph the first pass peaked at 2.2 GB and Relate's server at 674 MB. The demo seed needs less |
| Ports | 8000 for the lab; 7687, 7474 and 7475 for Relate; 17687 and 17474 for the bundled Neo4j. All on 127.0.0.1, and each is a setting in `.env` |
| Time | The first image build took 383 s from a clean clone. After it, `compose up` to a reviewed push took 160 s |
| Telemetry | The lab's containers set `DO_NOT_TRACK=1`, so Relate sends no usage metrics from them. The standalone `relate` CLI sends anonymous metrics by default: set `DO_NOT_TRACK=1` or run `relate telemetry off` |

**People.** [The walkthrough](demo/README.md) takes two minutes. Then open the demo run's
store with nothing else installed: [`demo/store.rl`](demo/README.md#inspect-it-yourself), `relate info`, four
queries. To run the whole loop on your machine:

```
git clone https://github.com/EastridgeAnalytics/subgraph-lab
cd subgraph-lab
cp .env.example .env                 # change the ports in it if another Neo4j already uses them
docker compose --profile local up    # builds the image, makes the seed, starts Neo4j and the lab
```

Open `http://localhost:8000`, then Connect, Slice, Run, and Review and push. With Aura instead of the bundled Neo4j, set `NEO4J_URI`,
`NEO4J_USERNAME`, `NEO4J_PASSWORD` and `NEO4J_DATABASE` in `.env`. A new instance is empty, so load the demo bank
into it once, then start the lab:

```
docker compose build lab                                  # the image holds the loader
docker compose run --rm lab python -m generator.load      # fills an empty instance; refuses one holding other data
docker compose up
```

Ports and every setting are on [07](architecture-docs/07-what-runs-where.md).

**Agents.** Point Claude Code, or any agent that reads a repo, at this folder. `CLAUDE.md` and
[`architecture-docs/README.md`](architecture-docs/README.md) say what to read first. With
the lab running, give the agent its two MCP servers, Relate's at `http://localhost:7475/mcp` and the lab's at
`http://localhost:8000/mcp` by default, and run `relate skill install` so it knows Relate's commands. To read the
demo store alone, `relate serve --database demo/store.rl --mcp stdio --read-only`. [`demo/agent.md`](demo/agent.md)
is one session doing the whole job that way; [`demo/agent/`](demo/agent/) holds the config it ran with.

**Working on the code.** `uv sync`, then `uv run python tools/install_relate.py` for the Relate CLI,
`uv run pytest -q`, and `npm test` in `app/`. A change that makes a page wrong changes the page in the same commit.

MIT, see [`LICENSE`](LICENSE). Built with Relate by Neo4j, for the Relate by Neo4j hackathon.
