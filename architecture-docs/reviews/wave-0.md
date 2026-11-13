# Wave 0 · pins, scaffold, contracts

12 November 2026. The director alone, as [97](../97-development-plan.md) says.

**Verdict.** Done. Every Relate claim was run against 1.0.1 and the pages corrected
([the table](wave-0-relate-1.0.1.md)). The scaffold, the ten contracts and the shared helpers are in, with 23
Python tests and 2 app tests green on the fixture.

## What landed

| Part | Where |
|---|---|
| Pins | `pyproject.toml` and `uv.lock`: Python 3.12, `relate-db==1.0.1`, PyTorch 2.12 CPU, pyg-lib 0.7.0, PyG 2.8, NetworkX 3.7, statsforecast 2.1. `tools/install_relate.py` puts the checked Relate 1.0.1 binary on the venv's path |
| Settings | `lab/settings.py`, every setting and judgment number, mirrored in `.env.example` and on 07 |
| Contracts | `lab/contracts/`: run manifest, staged table, recipe, REST routes, the eight MCP tools, factor file, policy, evidence and summary, node shapes and keys. JSON schemas in `lab/contracts/schemas/`; a test fails when they drift |
| Shared helpers | `lab/common/`: ids and keys, hashes, `open_store`, `RunPaths`, `relate_cli` with `DO_NOT_TRACK=1`, `credentials_file`, the Bolt driver, Arrow to Polars, the logger |
| The run facade | `lab/runs.py`: one object the examples, the API and the tools use, with the table of which track provides which function |
| Fixture | `tests/fixtures/`: 200 accounts, 2,000 payments, rings R1 and R2, look-alike L1, and the ground truth; `conftest.py` builds it as a Relate store once per session |
| App | `app/`: React 18, Vite 6, TypeScript, Tailwind 4, the router with the seven screens in order, the API client with the mock switch, the contract's TypeScript types, Vitest |
| Packaging | `Dockerfile` (the lab plus the checked Linux binary), `compose.yaml` (`lab`; `generate`, `seed`, `neo4j` under `local`), `.env.example`, `.github/workflows/ci.yml` |

## Decisions

18 to 28 on [08](../08-decisions.md). The ones that change the design: no separate `relate` service (21), a push
store apart from the run store (22), timestamps as strings in the push store (23).

## Left for later waves

- `python -m generator` and `generator/import.sh` are 1a's; `docker compose --profile local up` runs once they land.
- `lab.pipeline.run_recipe` is the director's, written when the pieces exist, at the end of wave 2.
- The usage limit was not reached. No builders ran in this wave.
