# subgraph-lab, in plain English

<p class="eyebrow">Eastridge Analytics · subgraph-lab · built with Relate by Neo4j · November 2026</p>

Relate first, then why we built on it, then what you're looking at. The figures open up when you click them.

<div class="verdict" markdown="1">

**The short version**

- **Relate is a graph database that runs inside a program, with the whole graph in one file.** Neo4j released it as open source in November 2026. It copies a piece of a large Neo4j graph out, and puts results back.
- **subgraph-lab is built on that trip.** Pull a slice out, learn from it with open-source models, review what would change, push it back. Nothing touches the live graph until a person confirms.
- **What you're looking at is one use case on top: fraud detection and prevention.** Rings of accounts that share a device or an address and pass money round. It is the use case we know best.
- **An AI agent can do the legwork.** It reads the file through Relate and can change nothing in Neo4j without the same review a person needs.
- **It is open source, and it is our entry in Neo4j's Relate hackathon.** Anyone with a Neo4j graph can run it.

</div>

## What Relate is

Relate is a new graph database from Neo4j. A graph database stores things and the links between them: an account and the device it logged in from, a payment and the two accounts on either end. Neo4j is the server version. It holds the whole graph for a company. Relate is the small one. It runs inside a program, keeps a whole graph in one file, and needs no server.

Three things make it useful here.

1. It speaks the same query language as Neo4j, Cypher, and has search built in, by words and by meaning.
2. It pulls a piece of a Neo4j graph into a file with one command and pushes results back with another. The push shows what it would write before it writes anything.
3. An AI agent can open the file. Relate ships a server that speaks MCP, the way agents talk to tools, and a skill that teaches the agent its commands.

Relate's own list of what it is not is just as useful. The graph has to fit in memory. One graph per file. One user or agent at a time. It is not a warehouse. That is the right shape for this job, where every run gets its own store: a folder in the lab, and one file for the demo run in the repo.

Relate reached 1.0 on 11 November 2026 under the Apache 2.0 license. Figure 1 is the whole thing in one picture.

{{FIG:relate}}

## Why we built subgraph-lab on it

We build fraud detection on Neo4j for a living, and every engagement hit the same wall. The production graph holds the whole population, millions of accounts. The value comes from adding to it: a risk score on each account, the groups accounts form, a written reason. You add that a piece at a time, and you want to try a model on a piece before you trust it on everything. Nobody runs experiments on the live graph.

Before Relate that meant a second graph server, or exporting tables and losing the links. Relate makes the piece a file. Pull a slice, work on the file, show what would change, push back. That is the whole idea of subgraph-lab. Relate's README describes pull and push as exactly that trip, mirroring a subgraph between Relate and Neo4j, and the lab adds the learning in the middle.

| What Relate does | What the lab does with it |
|---|---|
| Pull | A slice of the graph into a fresh store for each run |
| Cypher and search | Find the part that matters: by words, by meaning, then the links out |
| The file inside Python | Hand the slice to open-source graph models |
| Serve, read only | The app and the agent read the file while the lab writes it |
| Push, dry run first | A person sees the plan, then one confirm |
| The MCP server and the skill | An agent reads the evidence for itself |

## What you're looking at: fraud detection on top of Relate

Nothing above is about fraud. The loop works on any graph. We picked fraud for the first use case because it is the one we know best, and because a fraud ring is the clearest case of a problem you can only see in the links. The rest of this page is that use case.

### Fraud hides in the connections

Most fraud checks look at one account at a time. Is it new? Did it move a lot of money? A ring is built to pass those checks. Each account in it stays small and ordinary.

What gives a ring away is what its accounts share: a device, an address, an email, and money that goes round between them. A graph keeps those links with the accounts, so they can be seen.

Figure 2 is ring R0006 from the demo data. The buttons above it show what a one-at-a-time check sees, and what the links show.

{{FIG:ring}}

{{IMG:graph}}

### What the lab does with the slice

The lab works on a copy, the part of the graph that matters for the question, and writes back only what a person approved.

1. **Pull.** Copy a slice of the graph from Neo4j into a fresh Relate store.
2. **Find.** Search it in plain words, then follow the links out from what matched.
3. **Learn.** Hand the slice to open-source graph models.
4. **Score and explain.** Give every account and every group a risk score, with the arithmetic shown, and a short summary of why, written only from the evidence.
5. **Review.** Show a person exactly what would change in Neo4j before anything is written. That preview is the dry run.
6. **Push.** Write it back after one confirm. Every change is marked with the run that made it, so a run can be taken back.

{{FIG:loop}}

The demo bank is made up: 10,000 accounts, 120,000 payments, 60 planted fraud rings and 60 honest groups that look like rings. The answer key is written down, so anyone can check the lab against it. In the walkthrough run, the top group of 172 is exactly ring R0006, no more accounts and no fewer, and 210 of the 211 alerts land on planted ring accounts.

The data is built to be found, so those numbers don't prove the lab beats anything. What they show is the whole loop working.

### Where AI fits, and where it stops

We gave an AI agent, Claude Code, the lab's tools and Relate's, and nothing else. Then we asked it in plain words to find the riskiest ring among accounts active since 20 October. It found R0006 in 13 turns and under four minutes, and pushed the result back with a confirm.

Two recorded sessions of the same job are on GitHub. [The person's session](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/demo/README.md) is the walkthrough: ring R0006 followed through every screen, with the screenshots from one real run. [The agent's session](https://github.com/EastridgeAnalytics/subgraph-lab/blob/main/demo/agent.md) is rendered from its own log, so nobody edited it by hand.

It also got one thing wrong. The summary it wrote quoted three numbers that weren't in the evidence, so the lab refused it and kept its own. Every summary passes that check, whoever writes it.

An agent should be able to read everything in the slice and change nothing in Neo4j without a review. Here that rule is in the code, not in the instructions the agent was given.

## Why it matters

- **For a fraud team.** Rings show up as groups, with the reason written down and the innocent explanation to rule out first.
- **For whoever answers for the data.** Nothing is written to Neo4j without a dry run and a confirm, every change carries its run, and a run can be taken back.
- **For a Neo4j team.** The value lands on the graph they already have, a slice at a time.

## Who built it

Tim Eastridge, who leads Eastridge Analytics, is a Neo4j certified expert, a Neo4j Ninja, and the author of *Graph Data Science with Python and Neo4j*. The team has five certified Neo4j experts. The largest graph we run is fraud detection and entity resolution for federal pandemic-spending oversight (PRAC), at 1 billion nodes and 2 billion relationships.

The factors, the group score and the evidence-first summaries here come from that work. No client's code, data or name is in it.

## Why now

- **Relate shipped.** 1.0 on 11 November 2026, and the trip from Neo4j to a file and back is a few commands.
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
| Relate: one file, no server, Cypher, search, pull and push, the MCP server and the skill, its own limits | [Relate's README](https://github.com/relatedb/relate), read on 16 November 2026 |
| Relate 1.0 on 11 November 2026, Apache 2.0 | Relate's releases and its license file |
| The Relate pieces the lab uses | `README.md`, "How it uses Relate"; `architecture-docs/00-overview.md` |
| Ring R0006: five accounts, one device, one address, 21 payment cycles, each account scored 0.90, the top group of 172 | `demo/README.md`, the walkthrough run |
| The demo bank, the answer key, 210 of 211 alerts | `README.md`, "What you will see" |
| The loop, step by step | `README.md`, `architecture-docs/00-overview.md` |
| 160 seconds from starting the lab to a reviewed push | `architecture-docs/98-measured.md` |
| The agent: 13 turns, 3.7 minutes, three numbers refused | `demo/agent.md`, `demo/agent/session.jsonl` |
| Read only, nine tools, one confirm | `lab/api/mcp_tools.py`, `lab/roundtrip/push.py` |
| The four packs | `architecture-docs/99-build-status.md`, the 2.0 row |
| The figures | `demo/about/figures.js`, built into this page by `demo/build_about.py` |
