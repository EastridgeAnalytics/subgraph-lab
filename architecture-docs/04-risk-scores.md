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

Inside each slice the lab groups accounts in two steps (decision 42), and NetworkX does the second.

- **Shared identifiers first.** Accounts joined by a device, an email or an address that at most `LAB_SHARED_MAX` accounts
  of the slice use are linked. Each connected component of at least the policy's `community.min_size` accounts is a
  community as it stands. A planted ring on one device is one community of its members, whatever else they pay. An
  identifier used by more accounts than that links none, and a component under `min_size` goes on to the next step.
- **Then payments, among the accounts left.** A payment links two accounts once, either way. Connected components come
  first, and Louvain runs only on the big pieces (more than `LAB_COMPONENT_MIN` accounts). **Louvain is seeded, so a
  re-run gives the same groups.** Louvain never sees an identifier community, because maximising modularity merges small
  clusters inside big graphs (the resolution limit), and fraud rings are small clusters.
- Each community is tagged by how it formed, `identifier` or `payment`: the `formed` column of `communities.parquet`, and
  `formed_by_identifier` (1 or 0) among the `score_parts`. It is not a staged column and not a part of the score. The
  measured page reports ring recall for each kind. To measure.
- On the seed slice (run `run-20261113T082716Z-52f819`, 6,546 accounts), the first communities came out as 29 with the
  largest at 758 accounts and a median of 112. Grouped by identifier first they come out as 65 (45 by identifier, 20 by
  payment) with the largest at 651 and a median of 6. A ring with no shared identifier still depends on Louvain on
  payments; the measured page says how many such rings come out whole.
- Community keys are `sha1(run_id | lowest member key)`. A later run makes new communities and leaves the old
  ones as history.
- Whole-graph communities with GDS Leiden on Aura Graph Analytics are release 1.1.

The community score's five parts are `member_risk` (the policy's `community.member_risk_share` mixes the share of members
at the alert threshold with their mean score), `shared_identifiers`, `cycles`, `new_accounts` and `speed`. Three of them
use the shared definitions in `lab/scoring/signals.py`, which the first pass's summary phrases and the evidence use too:

- **Cycles are time-respecting.** A cycle is a loop of at most `LAB_CYCLE_MAX_LENGTH` accounts where each pays the next and
  each payment comes after the one before it. A loop paid in the wrong order is not money that came back. Cycles are
  counted up to `LAB_CYCLE_COUNT_CAP` per community.
- **Money in and out within a day is the factor's.** The `speed` part and the summary phrase run
  `factors/in_and_out_within_a_day.cypher` with the factor's threshold (similar amounts within 24 hours). There is no
  second definition.
- **New accounts** are those opened within `LAB_NEW_ACCOUNT_DAYS` of the newest payment.

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
