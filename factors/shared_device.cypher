// Five or more accounts on one device. Returns one row per account on such a device.
// key: the account key; value: how many accounts share the device; evidence_ids: the device key.
//
// This file is the customer's to edit. It reads the mapping's default names: Account.account_id, Device.device_id,
// USES_DEVICE. Change them to match your graph.
// $threshold: the factor's threshold. $keys: a list of account keys to score, or null for every account.
MATCH (a:Account)-[:USES_DEVICE]->(d:Device)<-[:USES_DEVICE]-(other:Account)
WHERE $keys IS NULL OR a.account_id IN $keys
WITH a, d, count(DISTINCT other) + 1 AS sharing
WHERE sharing >= $threshold
RETURN a.account_id AS key, sharing AS value, [d.device_id] AS evidence_ids
