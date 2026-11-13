// Money in and money out of similar size within a day: an outgoing payment that follows an incoming one within
// 24 hours and is within 20% of its amount. One row per account.
// key: the account key; value: how many different outgoing payments matched an incoming one; evidence_ids: the
// matched payment keys, outgoing first, ten at most.
//
// This file is the customer's to edit. It reads the mapping's default names: Account.account_id, the TX
// relationship with tx_id, amount and ts. Change them to match your graph. The 24 hours (86400 seconds) and the
// 20% are below.
// $threshold: the factor's threshold. $keys: a list of account keys to score, or null for every account.
MATCH (:Account)-[i:TX]->(a:Account)-[o:TX]->(:Account)
WHERE ($keys IS NULL OR a.account_id IN $keys)
  AND duration.inSeconds(i.ts, o.ts).seconds >= 0
  AND duration.inSeconds(i.ts, o.ts).seconds <= 86400
  AND abs(o.amount - i.amount) <= 0.2 * i.amount
WITH a, i, o ORDER BY o.ts, i.ts
WITH a, collect(DISTINCT o.tx_id) AS outs, collect(DISTINCT i.tx_id) AS ins
WHERE size(outs) >= $threshold
RETURN a.account_id AS key, size(outs) AS value, (outs + ins)[0..10] AS evidence_ids
