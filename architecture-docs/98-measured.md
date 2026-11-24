# 98 · Measured

Every number the other pages tagged "to measure", measured. Each row names its record in `measured/`, written by
`tools/measure.py`. A record names the runs it read, the machine, the command and the raw values. Run
`tests/acceptance/check_measured.py` to check that every number here is in the record its row names (A8).

**These are one laptop's numbers on synthetic data.** They say what the lab does on the seed. They say nothing yet
about Aura, about real fraud, or about a person reading the screens.

## The machine

| What | Value | Record |
|---|---|---|
| Operating system | Windows 11, build 26200, Docker Desktop | `20261116-run-seed-whole` |
| Processor | 12 logical CPUs, 10 cores, no GPU | `20261116-run-seed-whole` |
| Memory | 63.7 GB | `20261116-run-seed-whole` |
| Relate | 1.0.1 | `20261116-run-seed-whole` |

## Pull

From a Neo4j 2026.08.1 in Docker on the same machine, into a fresh Relate store. The seed is the bundled Neo4j's.
The 1M graph is the generator's other size (`--size 1m`), seeded into a second Neo4j for this and then removed.
Relate is `relate pull` through the Query API. Bolt is the lab's fallback, run on purpose. Peak memory is the
`relate` process for Relate, and the lab's own growth for Bolt.

| Pull | Nodes | Relationships | Time | Peak memory | Record |
|---|---|---|---|---|---|
| Seed, payments since 15 August, Relate | 39,293 | 90,605 | 15 s | 219 MB | `20261116-pull-seed-aug15-relate` |
| Seed, payments since 15 August, Bolt | 39,293 | 90,605 | 66 s | 382 MB | `20261116-pull-seed-aug15-bolt` |
| The whole seed, Relate | 39,481 | 149,937 | 28 s | 274 MB | `20261116-pull-seed-all-relate` |
| The whole seed, Bolt | 39,481 | 149,937 | 108 s | 435 MB | `20261116-pull-seed-all-bolt` |
| The 1M graph, Relate | 276,111 | 999,343 | 124 s | 1,677 MB | `20261116-pull-1m-relate` |
| The 1M graph, Bolt | 276,111 | 999,343 | 400 s | 2,155 MB | `20261116-pull-1m-bolt` |
| From Aura | | | Waiting for a run on Aura | | |

- **Relate pulls three to four times faster than the Bolt fallback, at both sizes.** The fallback stays a fallback.
- A pull takes the schema's labels and relationship types, so a few unlinked nodes the generator writes stay behind.

## Relate's memory

| What | The whole seed | The 1M graph | Record |
|---|---|---|---|
| `relate serve --read-only=live`, answering | 113 MB | 674 MB | `20261116-serve-seed-all`, `20261116-serve-1m` |
| The same after a two-hop count over every account | 115 MB | 678 MB | `20261116-serve-seed-all`, `20261116-serve-1m` |
| That two-hop count's time | 0.5 s | 7.3 s | `20261116-serve-seed-all`, `20261116-serve-1m` |
| The lab's estimate (page 07's sizes, the top of the range) | 118 MB | 797 MB | `20261116-pull-seed-all-relate`, `20261116-pull-1m-relate` |
| The store on disk after the pull | 10 MB | 140 MB | `20261116-pull-seed-all-relate`, `20261116-pull-1m-relate` |

- **Relate serving 1M relationships takes 674 MB**, inside the 0.6 to 0.8 GB page 07 expected. The lab's estimate
  errs high, as it is meant to.

## The first pass at 1M relationships

The first pass on the 1M graph's pulled store: Node2Vec from PyG's example on every node, then a template summary
on every account. Peak memory is the lab's growth while it ran: the graph, the training and the summaries.

| What | Value | Record |
|---|---|---|
| Nodes trained on, and accounts embedded | 276,111, and 69,781 | `20261116-firstpass-1m` |
| Time | 1,047 s | `20261116-firstpass-1m` |
| Peak memory | 2,224 MB | `20261116-firstpass-1m` |

- **The first pass needs more than page 07 guessed for PyG training.** It peaked at about 2.2 GB, against about
  0.5 GB. With Relate's 674 MB beside it, an 8 GB laptop still holds the 1M graph.
- At this size, a whole-store read of every payment in one statement passed Relate's 256 MB limit on a single
  value. Whole-store reads now page through the account keys (wave 6).

## The loop on the seed

Two runs on the seed, payments since 15 August. The search slice is the loop as the video shows it: 50 hits and
one account step. The whole pull scores every account in the store.

| Step | Search slice | Whole pull | Record |
|---|---|---|---|
| Pull | 11 s | 22 s | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| First pass, Node2Vec and summaries | 111 s | 129 s | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| The TGN example | 2.5 s | 939 s | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Scoring, communities and summaries | 31 s | 65 s | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Review: the dry run and the delta | 3.5 s | 9.1 s | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Push, 1,626 rows | 2.7 s | Not pushed | `20261116-run-seed-loop` |
| The whole run | 174 s | 1,178 s | `20261116-run-seed-loop`, `20261116-run-seed-whole` |

- **The TGN example is the slow step.** It trains on every account's payments. On a search slice it takes seconds.

## Scores against the planted truth

The generator plants mule rings, structuring and pass-through rings, and honest look-alikes: families on one laptop,
payroll accounts, landlords. A ring counts as held when at least three of its members are in the slice, enough to
form a community. "High" means a community at or above the alert threshold, 0.6.

| Measure | Search slice | Whole pull | Record |
|---|---|---|---|
| Accounts scored | 763 | 9,932 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Planted accounts among them | 78 | 366 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Alerts | 79 | 373 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Alert precision | 0.99 | 0.98 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Alert recall | 1.00 | 1.00 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Look-alike accounts that alerted | 0 of 71 | 4 of 1,168 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Rings held | 12 of 31 | 60 of 60 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Rings that came out whole | 11 of 12 | 60 of 60 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Mule rings whole and high | 4 of 4 | 20 of 20 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Structuring rings whole and high | 1 of 1 | 20 of 20 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Pass-through rings whole and high | 6 of 7 | 20 of 20 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| Look-alike groups in a high community | 0 of 32 | 1 of 60 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |
| High communities that hold planted accounts | 11 of 11 | 60 of 61 | `20261116-run-seed-loop`, `20261116-run-seed-whole` |

- **The one look-alike in a high community is a family of three on one laptop** that sends money back and forth
  within a day. It looks like a ring by every shape the lab measures. That is what the look-alikes are for.
- **Recall is partly by construction.** The generator plants the patterns the rules look for, so a perfect recall
  here says the rules find what they were written to find. It says nothing about real fraud.

## The walk (A1)

From a clean clone with its own `.env`, `docker compose --profile local up`, then the app in a headless browser:
Connect, Models with TGN unticked, Slice by schema with payments since 20 October, Estimate, Run, then Review and
push and one confirm. `tests/acceptance/a1_walk.py` clicks and times it; the image was already built.

| Part | Time | Record |
|---|---|---|
| `docker compose up`: the seed made and loaded, Neo4j and the lab started | 49 s | `20261116-walk-subgraph-a1` |
| Connect, Models, Slice and the estimate | 1.5 s, 0.3 s, 4.3 s | `20261116-walk-subgraph-a1` |
| The run: pull, first pass, scoring, communities, summaries, review | 84 s | `20261116-walk-subgraph-a1` |
| Review and push, to "Pushed to the master graph" | 22 s | `20261116-walk-subgraph-a1` |
| From `compose up` to a reviewed push | 160 s | `20261116-walk-subgraph-a1` |
| Building the image first, on a machine that has never built it | 383 s | `20261116-image-subgraph-lab-dev-build` |

- **A1 holds for the machine: under three minutes with the image built, and under ten with the first build.** A
  person reads each screen, so their time is longer. That is not measured yet.
- With TGN ticked the run adds the TGN example's training on every account in the slice: the slow step above.

## The image

The `lab` image: CPU PyTorch, PyTorch Geometric, statsforecast, NetworkX, the Relate 1.0.1 binary and the built app,
on Debian 13 with Python 3.12.

| What | Value | Record |
|---|---|---|
| Size, compressed (what a registry would send) | 1.01 GB | `20261116-image-subgraph-lab-dev` |
| Size, unpacked on disk | 4.66 GB | `20261116-image-subgraph-lab-dev` |
| Build from a clean clone with no cache | 383 s | `20261116-image-subgraph-lab-dev-build` |
| Python distributions in the image, and those with a copyleft or unknown licence | 93, and 0 | `20261116-image-subgraph-lab-dev` |

- **No GPL in the image.** `tools/check_licenses.py` reads every distribution's licence from its metadata, inside
  the image. The Debian base carries the usual GNU tools, as every Debian image does; nothing of the lab links them.
  The bundled Neo4j is Neo4j's own Community image, pulled as it is and not part of this one.

## Not measured yet

| What | Why | Record |
|---|---|---|
| Pull and push timings from Aura, and loading the seed into Aura Free (A7) | Waiting for a run on Aura. The seed loader and its Aura test are ready | |
| Cost per summary through the Anthropic API | Waiting for a key; every summary so far came from the template | |
| How long a person takes to go from `docker compose up` to a reviewed push | The walk above is a script's clicks | |
