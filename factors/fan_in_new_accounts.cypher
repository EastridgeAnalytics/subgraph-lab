// Fan-in from new accounts: payments in from many different accounts that were 30 days old or less when they
// paid. One row per account.
// key: the account key; value: how many different new accounts paid it; evidence_ids: the first ten of their keys
// in order.
//
// This file is the customer's to edit. It reads the mapping's default names: Account.account_id, Account.opened,
// the TX relationship with ts. Change them to match your graph. The 30 days are below.
// $threshold: the factor's threshold. $keys: a list of account keys to score, or null for every account.
MATCH (s:Account)-[t:TX]->(a:Account)
WHERE ($keys IS NULL OR a.account_id IN $keys)
  AND s.opened IS NOT NULL
WITH a, s, duration.inDays(s.opened, t.ts).days AS age
WHERE age >= 0 AND age <= 30
WITH a, s ORDER BY s.account_id
WITH a, collect(DISTINCT s.account_id) AS sources
WHERE size(sources) >= $threshold
RETURN a.account_id AS key, size(sources) AS value, sources[0..10] AS evidence_ids
