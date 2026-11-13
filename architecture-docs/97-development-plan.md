# 97 · Development plan

The MVP on [99](99-build-status.md) gets built in seven waves by one director and a rotating set of builders. The
director is an Opus session in this repo. The builders are Sonnet subagents, each in its own worktree, each with
one brief. This page is the plan the director runs, and it is also the description of how AI was used that the
hackathon submission asks for.

Nothing on this page is built. Each wave ends with [99](99-build-status.md) updated and a report in `reviews/`.

## How the work runs

- **The director: one Opus session, on `main`, in the repo clone.** It reads `CLAUDE.md`, the docs README, pages
  00 to 08, 88, 99 and this page. In wave 0 it works alone. From wave 1 it writes one brief per task, starts
  builders with the Agent tool (the sonnet model, worktree isolation, in the background), **up to three at a
  time**, reads each report and diff, runs the suite and the whole loop itself, merges each track into `main` with
  a merge commit, fixes small things itself and sends a numbered list for anything bigger, updates 99 and 08 in
  the same commit as the code, and writes the wave report. **It does not stop for the owner until wave 6 ends.**
- **A builder: one Sonnet subagent per task.** It reads `CLAUDE.md`, the docs README, the pages its brief names,
  the contracts in `lab/contracts/`, and its brief. It works in its own worktree on a branch `track/<task>`,
  changes only the files its brief lists, writes the tests its brief names, and ends with its tests green and a
  report: every file touched, every contract touched, what it could not do, and a one-line proposal for its row
  on 99. It never edits 99, 08 or a contract.
- **The usage limit.** Three builders at a time. On the first usage-limit error the director lets the running
  builders finish, drops to one at a time for the rest of that five-hour window, and says so in the wave report.
  No busy polling: the director waits for the builder's notification.
- **The brief.** Every brief has the same eight parts: goal; pages to read; files you own; contracts you code
  against; done means; tests; what NOT to do; report back. A brief quotes the lines of the architecture page it
  implements, so the page and the code cannot drift apart silently.
- **Rules nobody breaks.** The seven rules on [00](00-overview.md) under "What holds everywhere". Plus: every
  Relate call runs with `DO_NOT_TRACK=1`; no GPL library in the image; Python 3.12 and CPU wheels; nothing merges
  without its test on the seed; every track owns its folder; only the director changes a contract, and the change
  is a decision on 08; a number nobody measured is tagged "to measure"; docs change only where the code made them
  wrong, in the same commit.
- **Three lessons from an earlier multi-agent build, baked into every brief.** One: the same job gets done three
  ways in three folders unless the shared helpers exist first, so wave 0 ships `lab/common/` and every brief
  names the helper to use. Two: judgment numbers (weights, thresholds, `k`, `h`, the cap, batch sizes, how many
  summaries) drift into code unless they have one home, so they live in `lab/settings.py` and `factors/`, and a
  builder that needs a new one adds it there with a line in the settings table on 07. Three: a fallback that
  hides a failure is a bug, so every fallback records which route ran and why, on the run manifest.
- **The local Neo4j first.** `docker compose --profile local up` runs a Neo4j community image with the seed, and
  `.env` points `NEO4J_URI` at it. Pull and push are built and tested against it. Aura credentials arrive from the
  owner later; the same code runs against Aura unchanged, and the Aura measurements on 98 wait for that.
- **On this machine.** Windows, Git Bash. Kill a server by PID with `taskkill //PID <pid> //F`, never by name.
  Worktrees come from the Agent tool. `uv` runs Python; `npm` runs the app.

## Wave 0 · the director alone: pins, scaffold, contracts

Goal: everything a builder needs on day one, and every Relate fact on the design pages checked against the
release that exists.

### 0a · pins

- `uv python install 3.12` (decision 18). `relate-db==1.0.1` from PyPI. The Relate CLI 1.0.1 for Windows from the release
  page, its hash checked against `SHA256SUMS`, on the path for the director; `relate-linux-amd64` the same way in
  the image. `DO_NOT_TRACK=1` in every shell, in `compose.yaml` and in the `Dockerfile`; `relate telemetry status`
  says disabled.
- The Relate server image: none is published, so the lab image carries the checked binary and runs
  `relate serve` itself (decision 21).
- A Neo4j community image with the Query API v2, pinned by tag. PyTorch 2.12 CPU wheels, pyg-lib 0.7.0 and PyTorch Geometric 2.8 (decision 19),
  statsforecast 2.1, NetworkX 3.7, pyarrow, polars, FastAPI, uvicorn, pydantic, PyYAML, the `anthropic` SDK, the
  `neo4j` driver, the `mcp` SDK, pytest, ruff. Node 22, React 18, Vite, TypeScript, Tailwind, Vitest.

### 0b · Relate 1.0.1 against the pages

Run each of these and fix the page where 1.0.1 differs, with a decision on 08 for each change:

- `relate pull --from FILE --query Q --database PATH` against the local Neo4j: which URI scheme the HTTP Query API
  needs, whether the store must be empty, which indexes are skipped.
- `relate push --dry-run --json` and a real push: the flags on 01, the batch size, what a failing batch reports,
  whether a keyed merge creates nodes.
- `relate serve --database PATH --bolt 7687 --http 7474 --mcp 7475` with the read-only flags: does the server see a
  commit the lab makes while it runs (the "live" question on 00, 07 and 08).
- The Python API: `relate.connect`, `Database`, `execute`, `execute_arrow`, how a result is read, the commit size
  limit, `UNWIND` batch loading.
- The hybrid search statement on 02, vector and full-text index creation, and `relate skill install`.
- Write the answers on `reviews/wave-0-relate-1.0.1.md`: a table of claim, page, result.

### 0c · scaffold

- `pyproject.toml` (uv, Python 3.12, every dependency pinned), `lab/` with `common/`, `roundtrip/`, `slice/`,
  `adapters/`, `scoring/`, `summaries/`, `api/`, `settings.py`, `contracts/`; `app/` from the Vite React
  TypeScript template with Tailwind, a router, and a mock API switch; `examples/`, `factors/`, `generator/`,
  `tests/` with `acceptance/`; `tools/check_docs.py`.
- `compose.yaml`: `lab` (port 8000, plus 7687, 7474 and 7475 for the `relate serve` it runs), `generate`, `seed`
  and `neo4j` under profile `local` (17687, 17474). Every host port is a setting (decision 26). `Dockerfile` for `lab`, CPU. `.env.example`
  with every setting on 07.
- A tiny fixture graph in `tests/fixtures/`: 200 accounts, 2,000 payments, two planted rings, one look-alike, as
  CSV and as a Relate store builder. Every unit test runs on it; `live` tests need compose.
- `ruff` and `pytest` configured; `npm test` runs Vitest. A GitHub Actions workflow runs both on push.

### 0d · contracts, in `lab/contracts/` as Pydantic models with JSON schemas beside them

| Contract | What it fixes |
|---|---|
| Run manifest | run id, recipe hash, routes taken (pull, push), batches with status, counts, model hash, factor file version, started and finished |
| Staged results table | a key column, `run_id`, `risk_*` columns, an optional `kind` (account, alert, community), an optional vector column |
| Recipe file | YAML: connect, slice (by schema, by Cypher, by search with `k` and `h`), models and factors, write-back |
| REST routes | `/connect/test`, `/slice/estimate`, `/slice/run`, `/models`, `/runs`, `/runs/{id}`, `/results/accounts`, `/results/communities`, `/results/community/{key}`, `/review/{run}`, `/push/{run}`, `/dispositions`, `/timeline` |
| The eight MCP tools | `egress`, `search_slice`, `score`, `preview_push`, `apply_push`, `explain_alert`, `describe_alert`, `graph_diff`, each with its input and output schema |
| `factors.yaml` | id, name, kind (cypher, python, model), query or function or source, threshold, weight |
| Policy file | how factors combine into `risk_score`, `risk_score_model`, `risk_score_rules`, `risk_factors` |
| Evidence object | the six fields on 05, with ids and totals only |
| Node shapes and keys | `Account` (the customer's key), `Alert` (`sha1(run_id \| account_id \| window)`), `RiskCommunity` (`sha1(run_id \| lowest member key)`), `LabRun`, and the member and alert links |
| Settings | the names on 07, loaded once in `lab/settings.py` |

### 0e · shared helpers, `lab/common/`

`settings`, `run_id()`, `alert_key()`, `community_key()`, `evidence_hash()`, `open_store(path)`, `arrow_to_polars`,
`neo4j_driver()`, `relate_cli(args)` (always with `DO_NOT_TRACK=1`, always capturing `--json`), and `log`. A test
for each. Every later brief names which of these to use.

Done means: `uv run pytest -q` green on the fixture, `npm test` green on the shell, `docker compose config` valid,
`relate --version` prints 1.0.1, the contracts import, 0b's table is written, 99 says wave 0 is done.

## Wave 1 · the data and the way in

Three builders at a time: 1a, 1b, 1c first; 1d when one finishes; 1e after 1b merges.

### 1a · generator and seed (`generator/`)

Accounts with devices, emails and addresses as spokes; payments over time; planted mule rings, structuring under
a round threshold, and rapid pass-through, each with a ring id; look-alikes: a family on one laptop, a payroll
account paying many people, a landlord collecting rent just under a round number. Two sizes: small, under
50,000 nodes and 175,000 relationships; and 1M relationships. Writes CSVs the `seed` service imports with
`neo4j-admin database import`, and `ground_truth.parquet` with every planted account and ring. Test: the planted
counts match the ground truth, both sizes, with a fixed seed. Row 2 on 99.

### 1b · pull and the store (`lab/roundtrip/pull.py`, `store.py`, `loader.py`)

`relate pull` through `relate_cli`, a fresh store under `data/stores/<run_id>`, credentials from the settings;
the Bolt fallback, parallel reads by key range through the driver, loaded with `UNWIND` batches; the route on the
manifest; vector and full-text index creation after every pull; the loader that starts at 20,000 rows, raises
until refused, halves on refusal. Tests: unit on the fixture; `live` against compose. Rows 3 and 4 on 99.

### 1c · adapters (`lab/adapters/`)

`table()` through `execute_arrow`, `to_pyg()`, `to_temporal()` in the TGB layout, `to_networkx()` weighted by
relationship kind, `to_series()` in Nixtla's long format. One test per adapter on the fixture, shapes and dtypes
asserted. Row 6 on 99.

### 1d · the app shell, Connect and Slice (`app/`)

The router, the layout, the "show code" drawer every screen uses, the Connect screen (test the connection, show
the pull route), the Slice screen's three modes with the estimate panel, all against the mock API. A Vitest
component test per screen. Part of row 12 on 99.

### 1e · push, dry run, delta, manifest (`lab/roundtrip/push.py`, `delta.py`, `manifest.py`)

`relate push --dry-run --json` parsed into the plan; the delta by sampling remote keys; the keyed push in batches
of 5,000; a manifest line per batch; resume from the first unfinished batch; `LabRun` written last; dispositions
carried; Bolt for retractions. Tests: a forced failing batch resumes and finishes (A6); the same run pushed twice
changes nothing (A3); `apply_push` without confirm raises (part of A5). Row 10 on 99.

Director at the end of wave 1: merge; `docker compose --profile local up`; pull the small seed into a store; stage
a hand-made table; dry run; push; open Neo4j Browser and see `LabRun`. Report in `reviews/wave-1.md`. 99.

## Wave 2 · find and score

### 2a · first pass and the search slice (`lab/slice/`)

Node2Vec from PyG's example on the slice, embeddings into the vector index; template account summaries into the
full-text index; the hybrid search statement from 02 with rank fusion, `k`, `h`, the cap and the estimate; the
three slicers behind one interface. Tests: a search slice keeps to `k` hits and `h` hops, estimate first (A4).
Rows 4 and 5 on 99.

### 2b · factors, policy, account scores (`factors/`, `lab/scoring/factors.py`, `policy.py`)

The schema check on `factors.yaml`; five rule factors in Cypher: five or more accounts on one device, a burst of
new accounts sharing an identifier, amounts just under a round threshold, money in and out within a day, fan-in
from many new accounts; the `python` and `model` loaders; the policy that makes the four `risk_*` values; the
staged table. Test: a factor added to the YAML shows up on the next run with no code change (A10). Part of row 8.

### 2c · the TGN and Node2Vec examples (`examples/tgn/`, `examples/node2vec/`)

PyG's own examples with the ending changed to `lab.stage(df)` and `lab.push()`, each under 100 lines, CPU, a few
epochs on the fixture as the test. The TGN score registers as the `tgn` factor. Part of row 7.

### 2d · the API and the MCP tools (`lab/api/`)

FastAPI routes per the contract; the eight tools served with the `mcp` SDK at `/mcp`; `apply_push` refuses
without `confirm=true`; the compose wiring for Relate's own MCP server, read-only, following the lab's writes.
Test: pull, search, score, preview and apply through MCP, and apply fails without confirm (A5). Row 11.

### 2e · the Run and Review screens (`app/`)

Run: one bar per step with live counts from `/runs/{id}`, resume on failure. Review and push: the plan and the
delta grouped by kind, one confirm. Part of row 12.

Director at the end of wave 2: the loop with real scores: pull, first pass, search slice, factors, stage, dry run,
push. Then the same loop driven by Claude Code as the agent through the two MCP servers. `reviews/wave-2.md`. 99.

## Wave 3 · communities, summaries, the rest of the app

### 3a · communities and the community score (`lab/scoring/communities.py`, `community_score.py`)

Links by payments and shared identifiers with the weights from settings; connected components, then seeded
Louvain on the big pieces; resolution from settings; community keys; the score from members and shape;
`RiskCommunity` rows and member links in the staged table. Test: ring recall against the ground truth on the
fixture, reported not asserted (A11). Rest of row 8.

### 3b · evidence and summaries (`lab/summaries/`)

The evidence object per alert and community, schema-checked and hashed; the three writers: the Anthropic SDK with
`LAB_MODEL`, the agent through `describe_alert`, the template; the three checks; the cache on hash plus model;
storage with hash, model and source; summaries into the full-text index; a community embedding as the mean of its
members'. Test: a summary with a planted number falls back to the template (A9); the cache makes no second call.
Row 9.

### 3c · the velocity factor and the NetworkX example (`examples/velocity/`, `examples/communities/`)

statsforecast per account into `velocity_anomaly`; cycles and components as the NetworkX example. Rest of row 7.

### 3d · the Models and Results screens (`app/`)

Models: pick models and factors, read each one's code or YAML, set weights and thresholds. Results: accounts and
communities tabs; a community's members on a graph, its score and parts, the summary with its source, the
evidence, "communities like this one", and the three decision buttons wired to `/dispositions`. Part of row 12.

### 3e · the Timeline screen (`app/`)

The slider that re-queries the store as of `t` on the server. Rest of row 12.

Director at the end of wave 3: the six beats of the video, end to end, on the small seed. `reviews/wave-3.md`. 99.

## Wave 4 · the director's review

No builders. The director reads the whole codebase against 00 to 08 and 88 and writes `reviews/mvp-review.md`:

- Does each page describe what the code does? Every mismatch, with the page and the line.
- A1 to A11, each run as a test or a script, with its result.
- The same job done more than one way: list every case with the files.
- Judgment numbers outside `settings.py` and `factors/`: list them.
- Fallbacks that hide a failure, dead code, credentials or evidence in logs, anything that leaves the container
  without a key.
- The app's path from `docker compose up` to a reviewed push, walked with Playwright, timed.
- A redesign list: where the structure should change, not just a line.

Each finding has a file, what happens, and the smallest fix. The redesign items say what the new shape is.

## Wave 5 · fix and redesign

Builders again, three at a time, one brief per finding group and one per redesign item. Docs change where the
code changed. A1 to A11 re-run by the director. `reviews/wave-5.md`. 99.

## Wave 6 · measured numbers, packaging, the stop

### 6a · the measured page (`architecture-docs/98-measured.md`)

Pull at 100k and 1M relationships against the local Neo4j; push timings; Relate's memory; ring recall; precision
and recall on accounts; how many look-alikes scored high; summary cost; image size; the 15-minute walk. Every
number names its run (A8). The Aura columns stay "waiting for credentials".

### 6b · packaging

The image size; `compose.yaml` and `.env.example` final; the README's Run section made true; `docker compose
--profile local up` from a clean clone to a reviewed push, timed (A1).

### 6c · the video script

`architecture-docs/video.md`: the six beats on 99's roadmap row 13 as a shot list with the exact clicks and the
exact Cypher. The recording is the owner's.

Then the director writes `reviews/wave-6.md`, updates 99, and stops with one message: **ready for your review.**
The Aura measurements, the public switch and the submission wait for the owner.

## Not in these waves

Everything on 88's out-of-scope list, and the 1.1, 1.2 and 2.0 rows on 99.

## Verification across the plan

- `tests/acceptance/` holds A1 to A11 as tests or scripts; `uv run pytest -q` and `npm test` green after every
  merge.
- `tests/acceptance/loop.py` runs the whole loop from compose up to a verified `LabRun` in Neo4j; the director
  runs it after every merge.
- `tools/check_docs.py` reports no flags; every Mermaid block parses.
- 99 names each row's state after every wave; 08 has a decision for every contract change.

## Starting the director

Open a Claude Code session with Opus in the repo clone and say:

> Read CLAUDE.md, architecture-docs/README.md, then 97. You are the director. Run waves 0 to 6 as 97 says.
> Up to three Sonnet builders at once; one at a time after a usage-limit error. Start on the bundled Neo4j.
> Do not stop for me until wave 6's report. Then say: ready for your review.
