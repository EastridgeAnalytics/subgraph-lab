# subgraph-lab

> **Read [`architecture-docs/`](architecture-docs/README.md) first.** Everyone, people and AI agents alike,
> starts there before reading or changing any code. [`99-build-status.md`](architecture-docs/99-build-status.md)
> says what is built, what is partly built and what is not.

subgraph-lab is a container that runs beside Neo4j Aura. It pulls a subgraph into Relate, finds the part that
matters with hybrid search, scores the accounts and the communities they form, writes a short summary of why a
community could be risky, and pushes all of it back to Aura after a dry run. Then it takes the next slice.

Built with Relate by Neo4j. An entry for the Relate by Neo4j hackathon, due 11 December 2026. MIT.

## What's in it

A) **Pull and push.** `relate pull` copies whatever a Cypher query returns from Aura into a fresh Relate store.
`relate push` merges the results back on keys, in batches, after a dry run you can read. It never deletes.
Parallel Bolt reads and writes are the fallback.

B) **Find.** A vector search and a full-text search in one Cypher statement, fused by rank, then a few hops out
from each hit. That subgraph is what the models work on. Slicing by labels and a time window, or by your own
Cypher, works too.

C) **Hand off.** Adapters turn the slice into an Arrow table, a PyTorch Geometric graph or event stream (the
Temporal Graph Benchmark layout), or a NetworkX graph. Four worked examples, each under 100 lines, each ending in
stage and push. No model code of our own.

D) **Score.** Risk factors live in a YAML file: a Cypher rule, a Python function or a model, each with a threshold
and a weight. Add an entry and it is in every score on the next run, with no code change. A policy file turns the
factors into an account score. Communities are found inside each slice and get a score of their own.

E) **Explain.** The riskiest communities get a summary of 120 words or less: what the evidence shows, why that
pattern could mean risk, and the innocent explanation to rule out. Every number in it must appear in the evidence,
and every account it names must be a member. If a check fails, a template takes over.

F) **Review and push.** The dry-run plan, plus the lab's own delta: new alerts and communities, scores that moved,
bytes to send. One confirm. A manifest resumes a push that stopped. Reviewer decisions (confirmed, false positive,
needs more) go back to Aura with the push.

G) **The app and the agent.** Seven screens: Connect, Slice, Models, Run, Results, Review and push, Timeline. An
agent reads the store through Relate's own MCP server and skill, and acts through the lab's eight tools. Only
`apply_push` with confirm set touches Aura.

H) **Demo data.** A generator that plants mule rings, structuring and rapid pass-through, records the answers, and
plants legitimate look-alikes too, so scores cannot be perfect.

## Layout

| Folder | What it is |
|---|---|
| `architecture-docs/` | How subgraph-lab works. Read first; `99-build-status.md` is the state of the build |
| `lab/` | The Python service: pull, slice, adapters, scores, summaries, stage, push, the API and the MCP tools. Planned |
| `app/` | The React screens. Planned |
| `examples/` | The four worked examples. Planned |
| `factors/` | The risk factor file and its Cypher queries. Planned |
| `generator/` | The synthetic payments data with planted patterns. Planned |
| `compose.yaml` | The lab, Relate serving read-only beside it, and an optional bundled Neo4j. Planned |

## Status

Nothing is built as of 12 November 2026. The design is complete and lives in `architecture-docs/`. Every number
there comes from a vendor's documentation or is marked "to measure".

## Run

Two ways, once it exists. Neither works yet.

```
cp .env.example .env      # NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE for your Aura instance
docker compose up         # the lab on http://localhost:8000, Relate serving read-only beside it
```

```
docker compose --profile local up     # the same, plus a bundled Neo4j with the seed data, for anyone without an account
```

Ports, settings and what to expect from a first run are on [07](architecture-docs/07-what-runs-where.md).

## How the work runs

AI agents build the tracks in parallel, each against the contracts in the architecture docs. One person
integrates and runs the whole loop on every merge. The hackathon submission says how the agents were used.

## Licence

MIT. See [`LICENSE`](LICENSE).
