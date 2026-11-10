#!/bin/bash
# Runs once, on first start of an empty Postgres volume.
# One schema + one login role per service; a role can only touch its own schema(s).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE EXTENSION IF NOT EXISTS citext;
  CREATE EXTENSION IF NOT EXISTS pgcrypto;
EOSQL

# role : schemas it owns (space separated)
SERVICES=(
  "auth:auth"
  "ingestion:ingestion data"
  "block:blocks"
  "template:templates"
  "report:reports"
  "editor:editor"
)

for entry in "${SERVICES[@]}"; do
  svc="${entry%%:*}"
  schemas="${entry#*:}"
  role="rp_${svc}"

  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE ${role} LOGIN PASSWORD '${RP_DB_PASSWORD}';
    REVOKE ALL ON DATABASE ${POSTGRES_DB} FROM ${role};
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${role};
EOSQL

  for schema in $schemas; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
      CREATE SCHEMA ${schema} AUTHORIZATION ${role};
      ALTER ROLE ${role} SET search_path = ${schemas// /,};
EOSQL
  done
done

# Nobody gets implicit access to public.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  REVOKE ALL ON SCHEMA public FROM PUBLIC;
EOSQL
