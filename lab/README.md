# lab

The Python package: everything from the pull to the push. The pages in
[`architecture-docs/`](../architecture-docs/README.md) say why. This says where.

| Folder or file | What is in it | Page | Tests |
|---|---|---|---|
| `common/` | The helpers every module uses: settings, ids, the store, the Neo4j driver, the `relate` CLI with `DO_NOT_TRACK=1` on every call, batching, logging | [07](../architecture-docs/07-what-runs-where.md) | `test_common.py` |
| `contracts/` | The Pydantic models with their JSON schemas beside them: the run manifest, the staged table, the recipe, the routes, the tools, the factor and policy files, the evidence object, the node shapes | [08](../architecture-docs/08-decisions.md) | `test_contracts.py` |
| `roundtrip/` | `pull.py`, `loader.py`, `indexes.py`, `stage.py`, `delta.py`, `push.py`, `ledger.py`, `retract.py`, `queries.py` | [01](../architecture-docs/01-the-round-trip.md) | `test_pull.py`, `test_loader.py`, `test_indexes.py`, `test_stage.py`, `test_delta.py`, `test_push.py` |
| `slice/` | The first pass (embeddings and account summaries), the estimate, the three slicers behind one interface, hybrid search | [02](../architecture-docs/02-the-slice.md) | `test_first_pass.py`, `test_estimate.py`, `test_search.py` |
| `adapters/` | The slice as a table, a PyG graph, a TGB event stream, a NetworkX graph, Nixtla series. Whole-store reads page by account keys | [03](../architecture-docs/03-models-and-adapters.md) | `test_adapters.py` |
| `scoring/` | Factors, the policy, signals, velocity, communities, the community score, the scoring pipeline | [04](../architecture-docs/04-risk-scores.md) | `test_factors.py`, `test_policy.py`, `test_signals.py`, `test_communities.py`, `test_community_score.py`, `test_scoring_pipeline.py` |
| `summaries/` | The evidence object, the three writers, the checks, the cache, storage | [05](../architecture-docs/05-summaries.md) | `test_evidence.py`, `test_summaries.py` |
| `api/` | The FastAPI routes, the worker, the nine MCP tools, and `relate serve --read-only=live` on the run's store | [06](../architecture-docs/06-the-app-and-the-agent.md) | `test_api.py`, `test_mcp.py`, `test_relate_server.py` |
| `settings.py` | Every setting on 07, loaded once. Judgment numbers live here and in `factors/`, nowhere else | [07](../architecture-docs/07-what-runs-where.md) | |
| `pipeline.py`, `runs.py` | One run from pull to push, and the run folders under `data/runs/<run_id>` | [00](../architecture-docs/00-overview.md) | `test_pipeline.py` |

`python -m lab serve` runs the API, the built app, the MCP tools and `relate serve` for the current run.
`python -m lab run RECIPE` runs a recipe end to end and stops before the push.
