// The bundled Neo4j's uniqueness constraints (page 07, the `seed-schema` service). Run with cypher-shell after
// Neo4j is up. Every statement is IF NOT EXISTS, so running it again changes nothing. A uniqueness constraint
// also gives each key an index, so `relate pull` and the lab's Bolt fallback find nodes by key.
CREATE CONSTRAINT account_id_unique IF NOT EXISTS FOR (a:Account) REQUIRE a.account_id IS UNIQUE;
CREATE CONSTRAINT device_id_unique IF NOT EXISTS FOR (d:Device) REQUIRE d.device_id IS UNIQUE;
CREATE CONSTRAINT email_id_unique IF NOT EXISTS FOR (e:Email) REQUIRE e.email_id IS UNIQUE;
CREATE CONSTRAINT address_id_unique IF NOT EXISTS FOR (a:Address) REQUIRE a.address_id IS UNIQUE;
