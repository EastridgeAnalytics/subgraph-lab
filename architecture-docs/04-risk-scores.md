# 04 · Risk scores

Every account gets a score. Every community gets a score. Both are built from risk factors that anyone can add
to a file.

```mermaid
flowchart TB
  subgraph factors["Risk factors · factors.yaml"]
    rule["A rule in Cypher<br/>seven accounts on one device"]
    fc["A forecast in Python<br/>outflow far above the account's own pattern"]
    model["A model<br/>the TGN score"]
  end
  policy["Policy file<br/>weights · thresholds · how they add up"]
  account["Account score<br/>risk_score · risk_score_model · risk_score_rules · risk_factors"]
  links["Links inside the slice<br/>payments · shared devices, emails, addresses"]
  comm["Communities<br/>connected components, then seeded Louvain"]
  cscore["Community score<br/>members · shared identifiers · cycles · new accounts · speed"]
  node["RiskCommunity node<br/>score · summary · run id · member links"]
  rule --> policy
  fc --> policy
  model --> policy
  policy --> account
  account --> cscore
  links --> comm --> cscore --> node
```

## Three levels

| Level | What it is | Where it lands in Aura |
|---|---|---|
| Risk factor | One reason, scored 0 to 1, with its evidence. A Cypher rule, a Python function or a model produces it | Inside the evidence, not as its own property |
| Account score | The factors combined by the policy file. Every part stays visible | `risk_score`, `risk_score_model`, `risk_score_rules` and `risk_factors` on the Account |
| Community score | Built from the members and the shape of the group: how many members score high, the identifiers they share, money cycling inside the group, the share of new accounts, how fast money moves through | A `RiskCommunity` node with its score and summary, linked to its members |

## Adding a factor

A factor is one entry in `factors/factors.yaml`. The file is versioned and checked against a schema.

```yaml
- id: shared_device_5plus
  name: Five or more accounts on one device
  kind: cypher
  query: factors/shared_device.cypher
  threshold: 5
  weight: 0.30
- id: velocity_anomaly
  name: Outflow far above the account's own forecast
  kind: python
  function: factors.velocity:score    # statsforecast
  weight: 0.20
- id: tgn
  name: Temporal model score
  kind: model
  source: examples/tgn
  weight: 0.50
```

- **Add an entry, re-run the recipe, and the new factor is in every score and every summary. No code change.**
- **The weights and thresholds that ship are set for the demo data. On real data, the team that owns the review sets them.**
- The MVP ships five rule factors, the velocity forecast and the TGN score.

## Communities

Inside each slice the lab links accounts by payments and by shared devices, emails and addresses, with a shared
device weighing more than one payment. Then NetworkX finds communities.

- **Connected components first. Louvain runs only on the big pieces.**
- **Louvain is seeded, so a re-run gives the same groups.**
- Resolution is a knob. Methods that maximise modularity tend to merge small clusters inside big graphs, and
  fraud rings are small clusters. The slice keeps the graph small, and the measured page reports how many planted
  rings came out whole. To measure.
- Community keys are `sha1(run_id | lowest member key)`. A later run makes new communities and leaves the old
  ones as history.
- Whole-graph communities with GDS Leiden on Aura Graph Analytics are release 1.1.

## The reviewer's decision

Results has three buttons on every account and every community: confirmed, false positive, needs more.

- **The decision goes back to Aura with the push as `risk_disposition`, with who and when.**
- In 1.2 the next training run uses those decisions as labels, and false positives point at the factor to tune.

## Words on this page

| Word | Means |
|---|---|
| policy file | The rules for combining factors into an account score |
| modularity | What Louvain maximises: more links inside groups than between them |
| resolution | Louvain's knob for how small a group can be |
| look-alike | A legitimate pattern the generator plants so that scores are not perfect: a family on one laptop, a payroll account, a landlord |

## Where it lives

`factors/` for the file and its queries, `lab/scoring/` for the policy, the communities and the community score.
Planned.
