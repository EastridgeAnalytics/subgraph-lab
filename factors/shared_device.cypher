// Five or more accounts on one device. Returns one row per account on such a device.
// key: the account key; value: how many accounts share the device; evidence_ids: the device key.
MATCH (a:Account)-[:USES_DEVICE]->(d:Device)<-[:USES_DEVICE]-(other:Account)
WITH a, d, count(DISTINCT other) + 1 AS sharing
WHERE sharing >= $threshold
RETURN a.account_id AS key, sharing AS value, [d.device_id] AS evidence_ids
