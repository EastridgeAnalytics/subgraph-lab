# 08 · Decisions

Settled, then open. A later decision can replace an earlier one. The number stays.

## Settled

1. **The name is subgraph-lab and the licence is MIT.** The repo is `subgraph-lab`, renamed from `subgraph_engine`
   on 16 November 2026 (decision 46).
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
8. **Communities are found per slice with NetworkX**, connected components first, then seeded Louvain (grouped by shared
   identifier first since decision 42). igraph and leidenalg are GPL and stay out.
9. **Summaries come only from a checked evidence object**, carry the innocent explanation, and fall back to a
   template on any failed check. The Anthropic API writes them when a key is set, else the agent, else the
   template.
10. **Reviewer decisions go back to Aura as `risk_disposition`.** Retraining on them is release 1.2.
11. **One lab container**, serving the store read-only through Relate beside it (decision 21), the bundled
    Neo4j optional.
12. **The app is React 18, Vite, TypeScript and Tailwind on FastAPI.** Seven screens in the MVP.
13. **The agent reads through Relate's MCP server and skill, and acts through the lab's tools** (nine since
    decision 38).
    `apply_push` with confirm set is the lab's only writer to the master graph (the seed loader, decision 52, only
    fills an empty one).
14. **No model code of our own.** Adapters to PyTorch Geometric, the TGB layout, NetworkX and Arrow, and the
    libraries' own examples.
15. **No number is published until it is measured.** Until then it is tagged "to measure", and the README says so.
16. **Freeze on 9 December 2026, submit by 11 December, 6:00 PM PST.** The final rules (13 November) close entries
    then; judging runs 12 to 16 December and winners are named on 17 December.
17. **Moved to 1.1:** the Markdown review pack, the single-file run, the in-browser timeline, a local language
    model, Arrow Flight, whole-graph communities.

Decisions 18 to 28 come from wave 0, which ran every Relate claim against 1.0.1
([review](reviews/wave-0-relate-1.0.1.md)).

18. **Python 3.12.** NetworkX 3.7 requires it. Replaces 3.11 on 07 and 97.
19. **PyTorch 2.12 on CPU, with pyg-lib 0.7.0 from PyG's wheel index.** Node2Vec needs pyg-lib. 2.12 is the
    newest PyTorch with pyg-lib wheels for both Windows and Linux.
20. **The bundled Neo4j is `neo4j:2026.08.1` community.** It serves the Query API v2 that pull and push use.
21. **No separate `relate` service.** No Relate server image is published. The lab image carries the checked
    `relate` binary and runs one `relate serve --read-only=live` for the current run's store, restarting it when a
    new run starts. Bolt, HTTP and MCP keep their ports.
22. **Push runs from a push store, never from the pulled store.** The push store holds only keys, `risk_*`
    properties, `Alert`, `RiskCommunity` and `LabRun` nodes and their links, with a uniqueness constraint per
    label. Pushing the pulled store would re-send every pulled property and merge parallel payments.
23. **Timestamps in the push store are UTC ISO-8601 strings.** `relate push` 1.0.1 fails with HTTP 400 on any
    zoned datetime property.
24. **Resume means pushing again what did not land.** `relate push` has no resume flag. The manifest records
    which entities landed; a resume pushes the rest. Keyed merge makes a resent batch harmless.
25. **Two URIs.** `NEO4J_URI` is for the Bolt driver; `NEO4J_QUERY_URI` is the `http(s)` Query API for Relate,
    derived from `NEO4J_URI` when unset. `--allow-plaintext` is added only for an `http://` URI.
26. **Host ports are settings** with the documented defaults, because a machine may already run Neo4j on them.
27. **The lab reads through `relate.connect()`.** Its cursor has `data()`, `to_arrow()`, `to_df()` and
    `graph()`. `relate.Database.execute_arrow()` exists too; the lab does not need it.
28. **`LabRun` is its own push**, after everything else in the run has landed.
29. **The search surface has fixed names.** Accounts carry `risk_embedding` (pushed) and `lab_summary` (not
    pushed); the indexes are `account_embedding`, `account_summary`, `community_embedding` and
    `community_summary`, created after every pull with `LAB_EMBEDDING_DIM` dimensions.
30. **The Connect screen can switch the master graph for this session.** `POST /api/connect/use` keeps the
    credentials in the lab's memory only, never on disk and never in a response; `.env` stays the default.
31. **A search slice reads a run store whose first pass is done.** The estimate names its run, or uses the newest
    such run; with none, it says to pull a schema slice first. A run whose recipe has a search slices its own
    store after its first pass.
32. **The lab creates uniqueness constraints on its own labels only** (`Alert`, `RiskCommunity`, `LabRun`) on the
    master graph before the first push, `IF NOT EXISTS`, and the review lists them. Never on a customer label.
33. **The vector query comes from the graph, not from the words.** Node2Vec embeddings describe structure, so a
    text query is not embedded: its full-text hits are found first and their mean embedding is the vector query.
    An account query uses that account's own embedding and summary.
34. **Examples are importable.** `examples/` is a package; the first pass calls the Node2Vec example's `embed`,
    and the scoring reads the TGN example's output, so each model runs in one place.
35. **A push never creates a customer node.** Before it sends anything, `apply` checks every account key in the push
    store against the master graph over Bolt and refuses the whole push when any is missing.
36. **The community score's weights live in the policy file** (`community.weights`, `community.min_size`), beside
    the account policy. Each part is in [0, 1] and the score is their weighted mean.
37. **A push from the API or an agent needs a review newer than the last thing staged** (A2). Without one the
    route and the `apply_push` tool answer 409 and nothing is sent. `apply` itself still runs a dry run first.
38. **The agent's long tools queue their work and answer at once.** `egress` (the pull and the first pass) and
    `score` return the run id with status queued; a ninth tool, `run_status`, returns the manifest to poll. A
    blocking call longer than an agent's tool timeout left half-done runs behind (wave 2). `run_status` takes
    `wait_seconds` (up to 240) and answers early when the run stops running, so an agent waits without spinning.

Decisions 39 to 42 come from the wave 4 review ([mvp-review](reviews/mvp-review.md)).

39. **The account score is a noisy-or of weighted factors** (R1): `1 - Π(1 - wᵢ·sᵢ)`, over the rule factors for
    `risk_score_rules`, the model factors for `risk_score_model`, and all of them for `risk_score`. A factor that does
    not fire changes nothing and a new factor can only add. The policy file says `combine: noisy_or`; the weighted
    mean stays available. A weight is in [0, 1], how much that factor alone can say; a weight outside it is an
    error. The shipped file has three tiers: 0.65 for a rule enough alone, 0.20 supporting, 0.05 unproven.
40. **A recipe can choose its factors.** `factors` lists factor ids; unset means every factor in the file.
41. **Hops count accounts** (R2). One hop is one account-to-account step, by a payment or by a shared identifier;
    an identifier shared by more than `LAB_SHARED_MAX` accounts is no step. `LAB_SEARCH_HOPS` defaults to 1.
42. **Communities start from shared identifiers** (R6). Accounts joined by an identifier shared by at most
    `LAB_SHARED_MAX` accounts form the first communities; Louvain runs on the payment graph of the accounts left.
    Cycles count only when each payment comes after the one before it, and "money in and out within a day" has one
    definition (similar amounts) used by the factor, the summary phrase and the community's speed (R3).
43. **The Models screen shows real code.** `GET /api/models/{name}/source` returns a factor's Cypher or Python, or a
    model's example, read only, from paths inside the lab's own folders.

Decisions 44 and 45 come from measuring wave 5 on the seed ([wave-5](reviews/wave-5.md)).

44. **Fast money is a third way to form a community.** Between the identifier step and the payment step, two
    accounts the `in_and_out_within_a_day` factor fired on are linked by a payment the factor matched, and each
    component of at least `min_size` accounts is a community. The factor's evidence names the payments, so the
    pattern keeps one definition.
45. **The community score is a noisy-or of its parts**, like the account score: `community.combine: noisy_or`, each
    weight in [0, 1]. The shipped weights are 0.65 for the members' risk and 0.20 for each shape. The weighted
    mean stays available.
46. **The repo reads for the judge first.** Renamed to `subgraph-lab`. The root README opens on what a judge sees
    and links to `demo/`: the walkthrough of one planted ring as Markdown and as one HTML file, the run's store
    committed as `demo/store.rl` by `relate clone`, its recipe and manifest, and the scripts that remake the pages.
    Each code folder has a README. The "read the docs first" rule for builders moved to the README's last section
    and stays in `CLAUDE.md`.
47. **The agent's session is committed as its log, and its page is rendered from it.** `demo/agent/session.jsonl`
    is Claude Code's own output with only the two MCP servers allowed; `demo/build_agent.py` writes
    `demo/agent.md`. Nobody edits the page. The README states where the lab goes next and points at 99's 2.0 row.
48. **A plain-English page sits beside the README for readers who don't read code.** `about.html` is built by
    `demo/build_about.py` from `demo/about/about.md` and `demo/about/figures.js`, in Eastridge Analytics'
    technical-figures style. Its footer credits that skill with no link, because the skill's repo is private. This
    repo went public on 16 November 2026 and GitHub Pages serves the page from `main`, where the README links it.
    `.nojekyll` at the root makes Pages serve every file as written.
49. **The Relate pin follows Relate's release at submission.** The CLI, `relate-db` and the image's binary moved
    together to 1.1.0 on 18 November 2026, after the wave-0 rows and the tests ran on it
    ([review](reviews/relate-1.1.0.md)). `tools/install_relate.py` holds the version, and a test keeps the three in
    step. 98 names the version each number was measured on.
50. **The confirm is the JSON boolean `true` at every door.** The REST body, the `apply_push` tool and `run.push`
    refuse a string, a number or null. Before, `"yes"` and `1` were read as true. The MCP SDK checks a call against a
    model it builds from the tool's signature, so the signature carries each field's constraints: `StrictBool` on
    `confirm`, and the range of `wait_seconds`.
51. **One agent runner for every shell.** `demo/agent/run.py` replaces the shell script, which needed `sh`. It
    reads the two servers' ports from the lab's settings, the ones Compose publishes, so the agent's config follows
    the running lab. The prompt goes in on stdin and the config in a file, so nothing depends on the shell's quoting.
    It replaces the committed log only after a session that ends in a result with the two servers' tools and nothing
    else. `run.sh` stays as a wrapper.
52. **The demo seed has its own loader for an empty database, and it is not the lab.** `neo4j-admin import` cannot
    reach Aura, so `python -m generator.load` writes the generator's CSVs over Bolt: the same labels, types, property
    names and value types as the import, merging on each key, so a retried batch or a second run creates nothing. It
    reads first and goes on only when the database is empty or holds this seed and nothing else. A label or type
    outside the seed's, or a node whose key is not one of its keys, is refused before anything is written. A test
    loads the seed both ways into a throwaway Neo4j and requires the same graph. A7 runs it on Aura.

## Open

| Question | What depends on it | Who |
|---|---|---|
| TGB's licence (no licence field on PyPI) | Whether the name goes in the README. The layout works either way | The integrator |
| `relate pull` speed at 100k and 1M from Aura | Whether Bolt stays a fallback or becomes the default | Data plane, week one |
| Aura Free's real limit | The seed size | When the seed loads |
| Whether pushed vectors land the way Neo4j's vector index wants | "Similar accounts" on Aura after the push | Data plane |
| Cost per summary | The default of 20 communities and 50 alerts | Scoring, measured |
| The first factor list, its weights, and the wording rules for summaries | What ships in `factors.yaml` | The project owner |
