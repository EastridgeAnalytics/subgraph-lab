# Architecture docs

Read these first, people and agents, before changing code. Each page says what is designed and what runs today.
A code change that makes a page wrong changes the page in the same commit. A judge? Start at the
[root README](../README.md) and [`demo/`](../demo/README.md), then come here for how it works.

Read them in order. 00 is the picture. 01 to 07 follow the data through one run. 08 is what was decided and what
is open. 88 is the product requirements. 97 is the plan the build ran by. 98 is every number. 99 is the state of
the build.

## How it works

| Page | What it answers |
|---|---|
| [00 Overview](00-overview.md) | What subgraph-lab is, the one picture, what holds everywhere, the words we use |
| [01 The round trip](01-the-round-trip.md) | How a subgraph gets from Aura into Relate, and how results get back |
| [02 The slice](02-the-slice.md) | How search picks the part of the graph the models work on |
| [03 Models and adapters](03-models-and-adapters.md) | How the slice reaches open-source models, and what comes back |
| [04 Risk scores](04-risk-scores.md) | Factors, account scores, communities and community scores |
| [05 Summaries](05-summaries.md) | How a community gets a written reason, and the checks on it |
| [06 The app and the agent](06-the-app-and-the-agent.md) | The seven screens, and what an agent can read and do |
| [07 What runs where](07-what-runs-where.md) | The containers, ports, settings and limits |

## How it was built

| Page | What it answers |
|---|---|
| [97 Development plan](97-development-plan.md) | The waves, the director and the builders, how the work ran |
| [Reviews](reviews/) | Each wave's report, the Relate 1.0.1 check, and the director's review of the whole codebase |
| [Video](video.md) | The two-minute shot list: six beats, the clicks and the Cypher |

## Evidence and scope

| Page | What it answers |
|---|---|
| [98 Measured](98-measured.md) | Every number, measured on one machine, each tied to its record in `measured/` |
| [99 Build status](99-build-status.md) | What is built, partly built and not; the roadmap |
| [88 Product requirements](88-product-requirements.md) | MVP 1.0: requirements, acceptance tests, scope |
| [08 Decisions](08-decisions.md) | Settled and open |

Relate's own documentation is the source for every `relate` command and Cypher feature on these pages:
[relatedb.ai/docs](https://relatedb.ai/docs/). Where a page says "from the docs", it means that. Where it says
"to measure", nobody has measured it yet.
