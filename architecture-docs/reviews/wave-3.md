# Wave 3 · communities, summaries, the rest of the app

13 November 2026. Five tracks, three builders at a time, as [97](../97-development-plan.md) says.

**Verdict.** Done. The six beats of the video run end to end on the seed: the seeded graph, the pull, the search with
its hops, the models and factors scoring accounts, communities with written summaries, the dry run and the delta,
and back in Neo4j a `RiskCommunity` with its score and summary linked to its members, then "communities like this
one". The default suite is 613 tests, the live suite 30, the app 204, and A2 to A7 and A9 to A11 pass in
`tests/acceptance/`; all green on `main`.

## What landed

| Track | What | Where |
|---|---|---|
| 3a | Communities per slice (connected components, then seeded Louvain on the big pieces), a five-part community score weighted by the policy file, `RiskCommunity` rows and member links, `ring_recall` | `lab/scoring/communities.py`, `community_score.py` |
| 3b | One evidence object per alert and community, schema-checked and hashed; the three-part template; five checks; the Anthropic API writer with a JSON schema and a template fallback; `describe` for an agent's text; the cache; community summaries in the full-text index | `lab/summaries/` |
| 3c | The velocity factor (statsforecast's Croston over every account at once) and the NetworkX example | `lab/scoring/velocity.py`, `examples/velocity/`, `examples/networkx/` |
| 3d | The Models and Results screens | `app/src/screens/` |
| 3e | The Timeline screen | `app/src/screens/` |

## The loop on the seed

Run `run-20261113T082716Z-52f819`, payments since 15 August 2026, the whole default recipe. Development timings on
this laptop with other work running; not measurements for the docs.

| Step | Result |
|---|---|
| Pull | Relate route, 39,293 nodes, 90,605 relationships, 47 s |
| First pass | 9,932 accounts embedded and summarised, 332 s |
| Search | 50 hits, 25,870 nodes within 2 hops |
| Models | TGN scored 4,100 accounts, 966 s |
| Score | 6,546 accounts, 97 alerts, all 7 factors ran, 29 communities, 70 template summaries, 111 s |
| Push | Every entity and the `LabRun` landed; the top `RiskCommunity` (11 members, 0.61, the mule ring's device and address) is on the master graph with its summary and member links |
| Like this one | Three communities by hybrid search over summaries and embeddings |
| Retract | The demo graph clean |

Against the seed's ground truth, inside this slice: alerts 97, precision 1.00, recall 0.36 of the 272 planted
accounts; 45 of 60 planted rings came out whole in one community, 48 mostly whole, and no look-alike group landed in a
community at or above the alert threshold. The largest communities hold 450 to 758 accounts, so "whole" often means
whole inside a very large group. Both go to the wave 4 review.

## Merges

- `app/src/api/mock.ts` conflicted between 3d and 3e (each appended a block); both kept.
- The app's test timeout went to 20 seconds: a graph-layout test ran past 5 under load.
- Nine older tests changed because the velocity factor now runs, a scored run now has communities, and
  `describe_alert` now checks text.

## How the wave ran

Three builders at a time; no usage-limit error. 3a and 3c started while wave 2 closed; nothing merged before wave 2's
report.

## Open, carried forward

- TGN took 966 seconds on this slice and the first pass 332; A1's 15 minutes needs a smaller slice or a lighter
  recipe for the walk (wave 6 measures).
- Community size and alert recall, in the wave 4 review.
