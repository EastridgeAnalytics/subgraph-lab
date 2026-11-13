#!/usr/bin/env bash
# The `seed` service (page 07): one neo4j-admin import of the generator's CSVs into the `neo4j` database.
#
# Runs inside neo4j:2026.08.1 with the CSVs on /import and the data directory on /data, before Neo4j first starts.
# A marker file on /data makes every later run do nothing, so `docker compose --profile local up` never wipes the
# seed. Delete the marker (or the volume) to seed again; --overwrite-destination replaces what is there.
set -euo pipefail

IMPORT_DIR="${IMPORT_DIR:-/import}"
DATA_DIR="${DATA_DIR:-/data}"
MARKER="${DATA_DIR}/.subgraph-lab-seeded"

if [ -e "${MARKER}" ]; then
  echo "seed: ${MARKER} exists, the bundled Neo4j is already seeded"
  exit 0
fi

for f in accounts devices emails addresses uses_device has_email has_address tx; do
  if [ ! -f "${IMPORT_DIR}/${f}.csv" ]; then
    echo "seed: ${IMPORT_DIR}/${f}.csv is missing; run the generator first (python -m generator --out ${IMPORT_DIR})" >&2
    exit 1
  fi
done

echo "seed: importing ${IMPORT_DIR} into database neo4j"
neo4j-admin database import full neo4j \
  --overwrite-destination \
  --nodes=Account="${IMPORT_DIR}/accounts.csv" \
  --nodes=Device="${IMPORT_DIR}/devices.csv" \
  --nodes=Email="${IMPORT_DIR}/emails.csv" \
  --nodes=Address="${IMPORT_DIR}/addresses.csv" \
  --relationships=USES_DEVICE="${IMPORT_DIR}/uses_device.csv" \
  --relationships=HAS_EMAIL="${IMPORT_DIR}/has_email.csv" \
  --relationships=HAS_ADDRESS="${IMPORT_DIR}/has_address.csv" \
  --relationships=TX="${IMPORT_DIR}/tx.csv"

# This script runs as root when compose bypasses the image entrypoint; the server runs as neo4j.
if [ "$(id -u)" = "0" ]; then
  chown -R neo4j:neo4j "${DATA_DIR}"
fi

touch "${MARKER}"
echo "seed: done"
