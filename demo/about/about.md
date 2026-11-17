# subgraph-lab, in plain English

<p class="eyebrow">Eastridge Analytics · subgraph-lab · built with Relate by Neo4j · November 2026</p>

What it is first, then why it matters, then why we're building it now. The figures open up when you click them.

<div class="verdict" markdown="1">

**The short version**

- **Fraud hides in the connections.** One account looks fine. Five new accounts on one device and one address, passing money round in a loop, is a ring.
- **subgraph-lab finds rings like that and explains them.** It copies a piece of a large graph out of Neo4j, lets open-source models learn from it, scores every account and every group, writes down why, and puts the results back after a person reviews them.
- **An AI agent can do the legwork.** It can read everything and change nothing without the same review and confirm a person needs.
- **We built it because every fraud engagement we run needs this loop.** It is open source, so anyone with a Neo4j graph can try it.
- **Now, because Relate reached 1.0 on 11 November 2026.** It makes the trip from Neo4j to one file and back a few commands.

</div>

## Fraud hides in the connections

Most fraud checks look at one account at a time. Is it new? Did it move a lot of money? A ring is built to pass those checks. Each account in it stays small and ordinary.

What gives a ring away is what its accounts share: a device, an address, an email, and money that goes round between them. A graph is a database that stores those links along with the accounts. Neo4j is the graph database we build on.

Figure 1 is ring R0006 from the demo data. The buttons above it show what a one-at-a-time check sees, and what the links show.

{{FIG:ring}}

{{IMG:graph}}

## What subgraph-lab does

Nobody wants to run an experiment on the live graph. subgraph-lab takes a slice out instead: the part of the graph that matters for the question, copied into one Relate file. It works on the copy, and it writes back only what a person approved.

1. **Pull.** Copy a slice of the graph from Neo4j into a fresh Relate file.
2. **Find.** Search it in plain words, then follow the links out from what matched.
3. **Learn.** Hand the slice to open-source graph models, the ones the research community already maintains.
4. **Score and explain.** Give every account and every group a risk score, with the arithmetic shown, and a short summary of why, written only from the evidence.
5. **Review.** Show a person exactly what would change in Neo4j before anything is written. That preview is the dry run.
6. **Push.** Write it back after one confirm. Every change is marked with the run that made it, so a run can be taken back.

{{FIG:loop}}

The demo bank is made up: 10,000 accounts, 120,000 payments, 60 planted fraud rings and 60 honest groups that look like rings. The answer key is written down, so anyone can check the lab against it. In the walkthrough run, the top group of 172 is exactly ring R0006, no more accounts and no fewer, and 210 of the 211 alerts land on planted ring accounts.

The data is built to be found, so those numbers don't prove the lab beats anything. What they show is the whole loop working.

## Where AI fits, and where it stops

We gave an AI agent, Claude Code, the lab's tools and Relate's, and nothing else. Then we asked it in plain words to find the riskiest ring among accounts active since 20 October. It found R0006 in 13 turns and under four minutes, and pushed the result back with a confirm. [The recorded session](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/demo/agent.md) is on GitHub, rendered from its own log.

It also got one thing wrong. The summary it wrote quoted three numbers that weren't in the evidence, so the lab refused it and kept its own. Every summary passes that check, whoever writes it.

An agent should be able to read everything in the slice and change nothing in Neo4j without a review. Here that rule is in the code, not in the instructions the agent was given.

## Why it matters

- **For a fraud team.** Rings show up as groups, with the reason written down and the innocent explanation to rule out first, like a household or a shared computer. A reviewer starts with the five accounts that matter.
- **For whoever answers for the data.** Nothing is written to Neo4j without a dry run and a confirm. Every change carries the run that made it. Nothing is deleted, and a run can be taken back.
- **For a Neo4j team.** The value is added on top of the graph they already have, a slice at a time, with models the open-source world keeps up to date.

## Why we built it

We build graph fraud detection on Neo4j for a living. Tim Eastridge, who leads Eastridge Analytics, is a Neo4j certified expert, a Neo4j Ninja, and the author of *Graph Data Science with Python and Neo4j*. The team has five certified Neo4j experts. The largest graph we run is fraud detection and entity resolution for federal pandemic-spending oversight (PRAC), at over 200 million nodes.

Every one of those engagements needed the same loop. Try a model on part of the graph, see the ring, and write the result back with a record of what changed. subgraph-lab is that loop, built once and in the open. No client's code, data or name is in it.

## Why now

- **Relate shipped.** Relate, Neo4j's new open-source graph database, reached 1.0 on 11 November 2026 under the Apache 2.0 license. It runs inside a program with no server, keeps a whole store in one file, and copies a slice to and from Neo4j with a dry run first. That round trip is what this lab is built on.
- **Agents are being handed company data.** An agent needs to see how the data connects, and it needs a check before it writes anything back. The lab gives it both.
- **The Relate hackathon.** Neo4j's Relate hackathon runs from 11 November to 11 December 2026. This is our entry.

## Where it goes next

Nothing in the loop is specific to fraud except the factor file and the demo data. The factor file is the list of warning signs and how much each one counts. The next step is a pack per use case: financial crime, churn, supply chain and identity. Same pull, same review, same push.

{{FIG:next}}

That is the plan, on the 2.0 row of [the build plan](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/architecture-docs/99-build-status.md). Today the lab ships the fraud pack.

## See it for yourself

- **[The walkthrough](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/demo/README.md).** Ring R0006 followed through every screen, with the screenshots from one real run.
- **[The agent](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/demo/agent.md).** The recorded session, from its own log.
- **[The store](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/demo/README.md#inspect-it-yourself).** The demo run's Relate file, 9.8 MB. Five minutes with the `relate` command line and nothing else to install.
- **[The code](https://github.com/EastridgeAnalytics/subgraph-lab).** One command starts the lab, Neo4j and the demo data on your machine. MIT licensed.

## Read from the repo

Every claim on this page, and where it comes from.

| What the page says | Where it comes from |
|---|---|
| Ring R0006: five accounts, one device, one address, 21 payment cycles, each account scored 0.90, the top group of 172 | `demo/README.md`, the walkthrough run |
| The demo bank, the answer key, 210 of 211 alerts | `README.md`, "What you will see" |
| The loop, step by step | `README.md`, `architecture-docs/00-overview.md` |
| 160 seconds from starting the lab to a reviewed push | `architecture-docs/98-measured.md` |
| The agent: 13 turns, 3.7 minutes, three numbers refused | `demo/agent.md`, `demo/agent/session.jsonl` |
| Read only, nine tools, one confirm | `lab/api/mcp_tools.py`, `lab/roundtrip/push.py` |
| The four packs | `architecture-docs/99-build-status.md`, the 2.0 row |
| Relate 1.0 on 11 November 2026, Apache 2.0 | Relate's public repository, its releases and its license |
| The figures | `demo/about/figures.js`, built into this page by `demo/build_about.py` |
