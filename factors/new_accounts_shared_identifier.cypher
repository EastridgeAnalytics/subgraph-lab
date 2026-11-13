// A burst of new accounts sharing an identifier: three or more accounts opened within 30 days of each other that
// share a device, an email or an address. One row per account and shared identifier.
// key: the account key; value: how many accounts on that identifier were opened within 30 days of this one,
// counting this one; evidence_ids: the identifier key. An account on two such identifiers gets two rows.
//
// This file is the customer's to edit. It reads the mapping's default names: Account.account_id, Account.opened,
// Device.device_id, Email.email_id, Address.address_id, USES_DEVICE, HAS_EMAIL, HAS_ADDRESS. Change them to match
// your graph, or delete a branch for an identifier you do not have. The 30 days are in each branch.
// $threshold: the factor's threshold. $keys: a list of account keys to score, or null for every account.
MATCH (a:Account)-[:USES_DEVICE]->(i:Device)<-[:USES_DEVICE]-(o:Account)
WHERE ($keys IS NULL OR a.account_id IN $keys)
  AND a.opened IS NOT NULL AND o.opened IS NOT NULL
  AND abs(duration.inDays(a.opened, o.opened).days) <= 30
WITH a, i.device_id AS identifier, count(DISTINCT o) + 1 AS value
WHERE value >= $threshold
RETURN a.account_id AS key, value, [identifier] AS evidence_ids
UNION ALL
MATCH (a:Account)-[:HAS_EMAIL]->(i:Email)<-[:HAS_EMAIL]-(o:Account)
WHERE ($keys IS NULL OR a.account_id IN $keys)
  AND a.opened IS NOT NULL AND o.opened IS NOT NULL
  AND abs(duration.inDays(a.opened, o.opened).days) <= 30
WITH a, i.email_id AS identifier, count(DISTINCT o) + 1 AS value
WHERE value >= $threshold
RETURN a.account_id AS key, value, [identifier] AS evidence_ids
UNION ALL
MATCH (a:Account)-[:HAS_ADDRESS]->(i:Address)<-[:HAS_ADDRESS]-(o:Account)
WHERE ($keys IS NULL OR a.account_id IN $keys)
  AND a.opened IS NOT NULL AND o.opened IS NOT NULL
  AND abs(duration.inDays(a.opened, o.opened).days) <= 30
WITH a, i.address_id AS identifier, count(DISTINCT o) + 1 AS value
WHERE value >= $threshold
RETURN a.account_id AS key, value, [identifier] AS evidence_ids
