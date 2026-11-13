# 08 · Decisions

Settled, then open. A later decision can replace an earlier one. The number stays.

## Settled

1. **The name is subgraph-lab and the licence is MIT.** The repo is `subgraph_engine`.
2. **The demo data is our own generator**, with planted mule rings, structuring and rapid pass-through, their
   ring ids recorded, and legitimate look-alikes beside them. Public fraud datasets with non-commercial licences
   stay out.
3. **Aura is the demo target.** A bundled Neo4j is the fallback for anyone without an account.
4. **`relate pull` and `relate push` are the paths in and out.** Parallel Bolt is behind both, and the route
   taken is recorded on the run.
5. **Slices can be search-defined.** Hybrid search picks the hits, hops pick up the rest, and the slice is what
   the models, the reviewer and the agent all see.
6. **Nothing reaches the master graph without a dry run and a confirm.** Push never deletes.
7. **Risk factors live in a YAML file** with a schema, and a policy file combines them. A new factor needs no
   code change.
8. **Communities are found per slice with NetworkX**, connected components first, then seeded Louvain. igraph and
   leidenalg are GPL and stay out.
9. **Summaries come only from a checked evidence object**, carry the innocent explanation, and fall back to a
   template on any failed check. The Anthropic API writes them when a key is set, else the agent, else the
   template.
10. **Reviewer decisions go back to Aura as `risk_disposition`.** Retraining on them is release 1.2.
11. **One lab container**, Relate's own image serving read-only beside it, the bundled Neo4j optional.
12. **The app is React 18, Vite, TypeScript and Tailwind on FastAPI.** Seven screens in the MVP.
13. **The agent reads through Relate's MCP server and skill, and acts through the lab's eight tools.**
    `apply_push` with confirm set is the only writer to the master graph.
14. **No model code of our own.** Adapters to PyTorch Geometric, the TGB layout, NetworkX and Arrow, and the
    libraries' own examples.
15. **No number is published until it is measured.** Until then it is tagged "to measure", and the README says so.
16. **Freeze on 9 December 2026, submit on 11 December.** The draft rules say 13 December. The plan uses the 11th.
17. **Moved to 1.1:** the Markdown review pack, the single-file run, the in-browser timeline, a local language
    model, Arrow Flight, whole-graph communities.

## Open

| Question | What depends on it | Who |
|---|---|---|
| TGB's licence (no licence field on PyPI) | Whether the name goes in the README. The layout works either way | The integrator |
| `relate pull` speed at 100k and 1M from Aura | Whether Bolt stays a fallback or becomes the default | Data plane, week one |
| Whether `relate serve --read-only=live` sees the lab's commits | Restart after each write, or not | Data plane, week one |
| Aura Free's real limit | The seed size | When the seed loads |
| Whether pushed vectors land the way Neo4j's vector index wants | "Similar accounts" on Aura after the push | Data plane |
| Cost per summary | The default of 20 communities and 50 alerts | Scoring, measured |
| The first factor list, its weights, and the wording rules for summaries | What ships in `factors.yaml` | The project owner |
