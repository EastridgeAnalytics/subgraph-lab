# Wave 6 · measured numbers, packaging

15 November 2026. The director alone, one measurement at a time on a quiet machine, as [97](../97-development-plan.md)
says. The records carry UTC dates, so they say 16 November.

**Verdict.** Done. Every number the pages tagged "to measure" is on [98](../98-measured.md) or marked waiting for
what it needs, and every number there names its record and run (A8 passes). A script walked the app from a clean
clone to a reviewed push (A1). Measuring found two defects that no test had caught, and both are fixed: the image
could not run `relate`, and the first pass failed at 1M relationships. The README's Run section is true, it says how
AI built the project, and the [shot list](../video.md) is ready for the recording.

## What landed

| What | Where |
|---|---|
| One record per measurement: the runs it read, the machine, the command, the raw values, the strings the page shows | `tools/measure.py`, `architecture-docs/measured/` |
| The measured page | [98](../98-measured.md) |
| A8: every number in 98's tables is in the record its row names | `tests/acceptance/check_measured.py`, and a test |
| A1: a clean clone brought up under its own compose project and walked in Chromium, timed | `tests/acceptance/a1_walk.py` |
| No GPL in the image: every installed distribution's licence, read inside the image | `tools/check_licenses.py` |
| CI builds the image, runs `relate` in it and checks its licences | `.github/workflows/ci.yml` |
| The shot list: six beats, the clicks and the Cypher | [video](../video.md) |
| The README's Run, status and how-AI-built-it sections; every "to measure" on the pages pointed at 98 | `README.md`, pages 01, 04 to 07 |

## Found by measuring

- **The image could not run `relate`.** The 1.0.1 Linux binary needs glibc 2.39 and the Debian 12 base has 2.36, so
  every pull, push and serve in the container failed. Every earlier wave ran the lab on the host, and the image was
  checked for its hash, never run. The base is Debian 13 now, the build runs `relate --version`, and CI builds the
  image.
- **The first pass failed at 1M relationships.** It read every payment in one statement, and the result passed
  Relate's 256 MB limit on a single value. Whole-store reads now page through the account keys, as slice reads
  already did; a test checks the pages give the same rows.
- **PyG needs more memory than page 07 guessed.** The first pass at 1M peaked near 2.2 GB, against about 0.5 GB.
  Relate itself served the 1M store in 674 MB, inside its guess. An 8 GB laptop still holds both.
- **Docker reports two sizes.** With the containerd image store, `inspect` gives the compressed content and the image
  list the unpacked layers. 98 shows both, each named.
- **One look-alike scores high on a whole pull:** a family of three on one laptop that sends money back and forth
  within a day. It is what the look-alikes are for, and 98 names it.

## The numbers, in short

All on [98](../98-measured.md) with their records.

| What | Result |
|---|---|
| Pull at 1M relationships | 124 s by Relate, 400 s by the Bolt fallback |
| The loop on a search slice, pull to push | 174 s |
| The whole seed scored, every account | 1,178 s, most of it the TGN example |
| Alerts on the whole seed | precision 0.98, recall 1.00 |
| Planted rings whole and in a high community, whole seed | 60 of 60 |
| A1 by script, `compose up` to a reviewed push | 160 s, and 383 s more for a first build |
| The image | 1.01 GB compressed, 4.66 GB unpacked, no copyleft package |

## Verification at the end

On `main` after the last code change, all green:

| Check | Result |
|---|---|
| The default suite | 702 passed, 1 skipped |
| The live suite, against the test Neo4j | 35 passed |
| The acceptance tests | 11 passed; loading the seed into Aura (A7) skipped, waiting for credentials |
| The app | 254 passed, and the typecheck and build clean |
| The loop, compose up to a verified `LabRun` and back | Passed, and the demo graph retracted clean |
| A8 on 98 | Passes |
| The docs check, and every Mermaid block rendered with Mermaid 11 | No problem file; 9 of 9 render |

## How the wave ran

No builders. Wave 5's loop had shown what running PyTorch jobs side by side does to a timing, so every measurement
here ran alone. The build paused on 13 November at the owner's request and resumed on 15 November. No usage-limit
error.

## Ready for the owner

These need you, in the order they unblock things:

1. **Aura credentials.** Then A7 loads the seed into Aura Free, and 98's Aura rows get their pulls and pushes.
2. **An Anthropic key,** for the cost per summary. Every summary so far came from the template.
3. **The recording,** from the [shot list](../video.md).
4. **The design calls in wave 5:** fast money as a way to form a community (decision 44), the noisy-or community
   score (decision 45), and the demo weights for accounts and communities. The walk on 98 unticks TGN; say if the
   demo should keep it.
5. **A person's walk** from `docker compose up` to a reviewed push, timed, for A1 as a person sees it.
6. **The public switch and the submission.**
7. **Cleanup, when you are ready.** The builders' worktrees beside the repo (`subgraph_engine-wt/`, every branch
   merged) and the clean clone in the temp folder are left for you. `git worktree remove` takes each one.

## Open, carried forward

- The TGN example is the slow step on a whole pull: 939 s for 9,932 accounts. A search slice keeps it to seconds.
- A search slice can cut a ring, so rings count as held only when three of their members are inside it.
