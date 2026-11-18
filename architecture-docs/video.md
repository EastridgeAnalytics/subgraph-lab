# Video · the shot list

One to two minutes, six beats, recorded by the owner. Each beat names what is on screen, the exact clicks and,
in Neo4j Browser, the exact Cypher. The voice-over lines are suggestions in plain words. Every number said aloud
comes from [98](98-measured.md) or from the generator's own counts.

The hackathon's rules for the video: it is the entrant's own work, filming, editing and graphics; any music or
image from someone else needs permission; and judges must be able to watch it without a login or a payment. An
unlisted YouTube link meets that.

## Before recording

- Pull `main` and run `docker compose --profile local up --build`, so the image carries Relate 1.1.0. The lab is on
  `http://localhost:8000`, Neo4j Browser on `http://localhost:17474` (or the ports in `.env`).
- For a clean beat 6, start from a fresh seed: `docker compose --profile local down -v`, then up again. That
  deletes the bundled Neo4j's data and the lab's runs, and compose makes the seed again. Without it, beat 6's last
  query still shows the newest run, but earlier runs stay in the graph.
- Run the whole loop once, off camera, so the first pass has run. The search in beat 3 needs it, and nothing on
  camera waits.
- Browser at 1280 by 800. Neo4j Browser in a second tab, logged in.

## The six beats

| # | Time | On screen | Clicks and Cypher | Voice-over |
|---|---|---|---|---|
| 1 | 0:00 to 0:15 | The graph | Neo4j Browser, the first query below | "Ten thousand accounts with their devices, emails and addresses. Somewhere in here are rings, and some honest look-alikes." |
| 2 | 0:15 to 0:35 | The pull | App: Connect, Test connection, Use this connection. Slice, By schema, Since 20 October, Estimate. Run this slice | "Pick a slice. The lab says how big it is before anything moves. Then `relate pull` brings it into Relate." |
| 3 | 0:35 to 0:55 | Find | Slice, By search, text "money in and out within a day across new accounts", Estimate | "Vector and full-text in one statement, fused by rank, then one step out from each hit. That is the part worth working on." |
| 4 | 0:55 to 1:15 | Score | Models: the factors with their weights, Show code on "Money in and out within a day". Run: every step done. Results, Accounts | "Risk factors in a YAML file, scored by Cypher, a forecast and a temporal model. Anyone can add one." |
| 5 | 1:15 to 1:35 | Explain | Results, Communities, the top community: its graph, its parts, its summary, its evidence. Communities like this one | "The ring, its shared device, the money that came back. A summary written only from checked evidence, with the innocent reading to rule out." |
| 6 | 1:35 to 2:00 | Review and push | Review and push: What changes, Push to the master graph, Confirm and push. Neo4j Browser, the second and third queries below | "A dry run, the delta, one confirm. Back in Neo4j: the community, its members, and the run that put them there. An agent can run the same loop through MCP, and its recorded session is in the repo." |

## The Cypher

Beat 1, accounts that share a device:

```cypher
MATCH (a:Account)-[:USES_DEVICE]->(d:Device)<-[:USES_DEVICE]-(b:Account)
WHERE a.account_id < b.account_id
RETURN a, d, b LIMIT 25
```

Beat 6, the top community with its members:

```cypher
MATCH (c:RiskCommunity)<-[:IN_RISK_COMMUNITY]-(a:Account)
WITH c, collect(a) AS members ORDER BY c.score DESC LIMIT 1
RETURN c, members
```

Beat 6, the run that wrote it:

```cypher
MATCH (r:LabRun)
RETURN r.run_id, r.route_pull, r.route_push, r.finished_at
ORDER BY r.finished_at DESC LIMIT 1
```

## Notes

- Beat 4 can swap Models for the agent: Claude Code with the lab's MCP tools running `search_slice` and `score`,
  then `preview_push`, and `apply_push` refusing without confirm. Ten seconds, if the cut allows it.
- Say "Built with Relate by Neo4j". No Neo4j logo, and nothing that implies an endorsement.
- Never say a precision, a recall or a time that is not on 98.
