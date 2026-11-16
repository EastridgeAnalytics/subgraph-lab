# subgraph-lab

Pull a subgraph from Neo4j Aura into Relate, let open-source models learn from it, and push the results back.

## Why this exists

A graph holds a whole population. Most of its value gets added on top, run after run: a risk score on every
account, an embedding on every node, an alert with a reason when something changes. Today, finding the part of
the graph worth working on is a one-off query, and getting that part into PyTorch Geometric or NetworkX and the
results back into Neo4j is a one-off script, with no record of what changed.

Relate's docs describe the round trip: pull a subgraph from Aura into Relate for a task, push the results back.
subgraph-lab is that round trip with learning in the middle. Pull a slice, find the part that matters with hybrid
search, hand it to the models the open-source community already maintains, review a dry run, push. Then the next
slice.

```mermaid
flowchart LR
  aura[("Neo4j Aura")] -->|relate pull| store[("Relate store")]
  store --> find["Find<br/>hybrid search plus hops"] --> learn["Learn<br/>PyG · NetworkX · forecasts"] --> score["Score and explain<br/>accounts · communities · summaries"] --> review["Review<br/>dry run · one confirm"]
  review -->|relate push| aura
```

> **Read [`architecture-docs/`](architecture-docs/README.md) first.** Everyone, people and AI agents alike,
> starts there before reading or changing any code. [`99-build-status.md`](architecture-docs/99-build-status.md)
> says what is built.

## What it does

A) **Pull and push.** `relate pull` into a fresh store. `relate push` back on keys, after a dry run. Never deletes.

B) **Find.** Vector and full-text search in one statement, fused by rank, then a few hops. That subgraph is the slice.

C) **Hand off.** Adapters to PyTorch Geometric, the Temporal Graph Benchmark layout, NetworkX and Arrow. No model code of our own.

D) **Score.** Risk factors in a YAML file anyone can extend. A policy file makes account scores. Communities get their own.

E) **Explain.** The riskiest communities get a summary of why, written only from checked evidence, with the innocent explanation to rule out.

F) **Review and push.** The dry-run plan plus the delta, one confirm, a manifest that resumes. Reviewer decisions go back with it.

G) **The app and the agent.** Seven screens. An agent reads through Relate's MCP server and acts through the lab's tools.

H) **Demo data.** A generator with planted rings and legitimate look-alikes, so scores cannot be perfect.

## Status

Waves 0 to 6 of the [development plan](architecture-docs/97-development-plan.md) are done, and
[99](architecture-docs/99-build-status.md) says what is built. Every number in the docs is measured on
[98](architecture-docs/98-measured.md), with the run it came from, or is marked "waiting" for what it needs: Aura
credentials or an Anthropic key. Code lives in `lab/`, `app/`, `examples/`, `factors/` and `generator/`.

Built with Relate by Neo4j, for the Relate by Neo4j hackathon, due 11 December 2026. MIT, see [`LICENSE`](LICENSE).

## How AI built it

One Claude Opus session directed the build from [97](architecture-docs/97-development-plan.md). Claude Sonnet
builders took one written brief each, worked in their own git worktrees, up to three at a time, and reported back.
The director merged each track after its tests passed on the seed, reviewed the whole codebase against the docs,
and briefed the fixes. Every wave's report is in [`architecture-docs/reviews/`](architecture-docs/reviews/). One
person owns the project and reviews it.

## Run

```
git clone https://github.com/EastridgeAnalytics/subgraph_engine
cd subgraph_engine
cp .env.example .env                 # change the ports in it if another Neo4j already uses them
docker compose --profile local up    # builds the image, makes the seed, starts Neo4j and the lab
```

Open `http://localhost:8000`, then Connect, Slice, Run, and Review and push. The first build downloads PyTorch for
CPU; its time and the image's size are on [98](architecture-docs/98-measured.md).

With Aura instead of the bundled Neo4j, set `NEO4J_URI`, `NEO4J_USERNAME` and `NEO4J_PASSWORD` in `.env` and run
`docker compose up`. Ports and every setting are on [07](architecture-docs/07-what-runs-where.md).

To work on the code: `uv sync`, `uv run python tools/install_relate.py` for the Relate CLI, `uv run pytest -q`, and
`npm test` in `app/`. `tests/acceptance/` holds A1 to A11.
