// Amounts just under a round threshold: three or more payments between 9,000 and 9,999.99, in or out of the
// account. One row per account.
// key: the account key; value: how many such payments touch it; evidence_ids: the first ten payment keys in order.
//
// This file is the customer's to edit. It reads the mapping's default names: Account.account_id, the TX
// relationship with tx_id and amount. Change them to match your graph. The amount band is below; the round
// threshold it sits under is 10,000.
// $threshold: the factor's threshold. $keys: a list of account keys to score, or null for every account.
MATCH (a:Account)-[t:TX]-(:Account)
WHERE ($keys IS NULL OR a.account_id IN $keys)
  AND t.amount >= 9000 AND t.amount <= 9999.99
WITH a, t ORDER BY t.tx_id
WITH a, collect(DISTINCT t.tx_id) AS ids
WHERE size(ids) >= $threshold
RETURN a.account_id AS key, size(ids) AS value, ids[0..10] AS evidence_ids
