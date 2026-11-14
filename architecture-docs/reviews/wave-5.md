# Wave 5 · fix and redesign

13 November 2026. Four builder tracks and the director's own fixes, from the [wave 4 review](mvp-review.md).

**Verdict.** Done. Every finding in the review is fixed or carried below with a reason. Two more redesigns came from
measuring the fixes on the seed: fast money as a third way to form a community (decision 44) and a noisy-or community
score (decision 45). On `main` the default suite is 699 tests, the live suite 35 and the app 254, all green. A2 to A7
and A9 to A11 pass in `tests/acceptance/`, and the loop runs from compose up to a verified `LabRun` and back.

## What landed

| Track | What | Review item |
|---|---|---|
| 5a | The account score is a noisy-or, `1 - Π(1 - wᵢ·sᵢ)`, with weights in [0, 1] in three tiers; a recipe chooses its factors (decisions 39, 40) | R1 |
| 5b | Communities form by shared identifier first; one definition each for time-respecting cycles, money in and out within a day, and new accounts (`lab/scoring/signals.py`); rows staged once; the summary cache key leaves out the run id; the judgment numbers in settings (decision 42) | R3, R6, sections 3 to 5 |
| 5c | A hop is one account step and `h` defaults to 1; the plan's created and updated counts come from the master graph; an account holding only a disposition gets the run id; the pull's reused counts are gone (decision 41) | R2, R7, section 5 |
| 5d | The app against the new lab: factors chosen on Models with their real source, paging on Results, both combine rules in the score parts, how a community formed as a tag, `h` 1 on Slice, a nav that wraps whole labels | R4, section 7 |
| Director | The source route (decision 43); fast money (decision 44); the noisy-or community score (decision 45); one newest-run lookup; one manifest save retry; the connection test reads the kernel's version; the pages made true again | Sections 3 and 7 |

## Measured on the seed while fixing

Development figures on this laptop, each naming its run. The measured page takes them again in wave 6.

- **Accounts (5a).** On the slice of run `run-20261113T082716Z-52f819`, alerts reached precision 0.98 and recall 1.00
  of the planted accounts, against 1.00 and 0.36 before. Four of 1,168 look-alike accounts alert. The recall is partly
  by construction: the generator plants exactly the patterns the rules look for.
- **Communities (5b, 44, 45).** The same slice, rings in a community at or above 0.6:

| Pattern | Before wave 5 | After 5b | After decisions 44 and 45 |
|---|---|---|---|
| Mule ring | 1 of 20 | 19 of 20 | 19 of 20 |
| Structuring | 0 of 20 | 10 of 20 | 19 of 20 |
| Pass-through | 0 of 20 | 1 of 20 | 7 of 20 |

  No look-alike group lands in such a community, and the highest look-alike scores 0.408. The 13 pass-through rings
  still missed had members outside this search slice, so their chains were cut before grouping. On a second seed (7),
  pulled whole, all 60 rings land at 0.6 or more and no look-alike does.
- **The loop after every merge.** Run `run-20261113T215414Z-ce764d`: a slice of 763 accounts at `h` 1, 79 alerts,
  precision 0.99, recall 1.00, no look-alike account alerted, 11 of the 12 rings the slice holds whole and scoring at
  or above 0.6, and every community at that line holding planted accounts. Then the push, the `LabRun` on the master
  graph, and the retract.

## Found on the way

- The first pass took 8,946 seconds in that loop, against 332 in wave 3. Node2Vec alone on the same store took 247
  seconds on a quiet machine. The loop had run beside the full suite, the app tests and two evaluations, all on
  PyTorch at once. No code changed it, and the measured page times the first pass on a quiet machine.
- `dbms.components()` answers one row per component on Neo4j 2026, so the connection test warned. It reads the
  kernel's row now.
- `LAB_PASS_THROUGH_HOURS` was read by nothing; money in and out within a day lives in the factor's query. It is gone.

## How the wave ran

Three builders at a time for 5a to 5c, then 5d alone. No usage-limit error. 5b and 5c stalled once each on a
silent stream and were resumed. 5d stalled after its tests passed; the director ran its typecheck and build and
merged it.

## For the owner

- **The weights are demo weights.** The account tiers (0.65, 0.20, 0.05) and the community tiers (0.65 for members'
  risk, 0.20 for each shape) were set on the seed and checked on seed 7. On real data the team that owns the review
  sets them.
- **Fast money and the noisy-or community score are design calls** (decisions 44 and 45), made because a ring that
  shows one pattern strongly scored under a ring that shows several weakly. Both can be turned back in the policy
  file (`combine: weighted_mean`); the fast-money step has no switch.

## Open, carried forward

- A1 (the 15-minute walk) and A8 (every measured number names its run) are wave 6.
- Aura: loading the seed (A7), pull and push timings from Aura. Waiting for credentials.
- Cost per summary waits for an Anthropic key; every summary so far came from the template.
