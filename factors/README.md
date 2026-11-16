# factors

Every way of spotting risk is a factor in `factors.yaml`: an id, a name, a kind (`cypher`, `python` or `model`),
what it runs, a threshold and a weight. `policy.yaml` says how the factors combine into an account's `risk_score`,
and how members and shape combine into a community's. Both are noisy-ors. Page
[04](../architecture-docs/04-risk-scores.md) has the arithmetic.

| Factor | Kind | Looks for |
|---|---|---|
| `shared_device_5plus` | cypher, `shared_device.cypher` | Five or more accounts on one device |
| `new_accounts_shared_identifier` | cypher, `new_accounts_shared_identifier.cypher` | A burst of new accounts sharing a device, an email or an address |
| `in_and_out_within_a_day` | cypher, `in_and_out_within_a_day.cypher` | Money in and out of similar size within a day |
| `just_under_threshold` | cypher, `just_under_threshold.cypher` | Amounts just under a round threshold |
| `fan_in_new_accounts` | cypher, `fan_in_new_accounts.cypher` | Fan-in from many new accounts |
| `velocity_anomaly` | python, `examples/velocity/` | A forecast of each account's payments, and how far the latest is from it |
| `tgn` | model, `examples/tgn/` | A temporal graph network's score per account |

Add a factor to the YAML and it runs on the next run with no code change (A10, `tests/test_factors.py`). The
Models screen shows each factor's code. The weights and thresholds are judgment calls, set for the demo data, and
belong to the project owner.
