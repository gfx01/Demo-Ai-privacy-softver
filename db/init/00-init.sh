#!/bin/bash
# First-start initialisation of the Postgres container (runs once, as the
# superuser). Creates one database per concern and least-privilege roles.
# Passwords come from the environment (.env), never from this file.
set -euo pipefail

: "${LITELLM_DB_PASSWORD:?}" "${PH_GATEWAY_DB_PASSWORD:?}" "${PH_PORTAL_DB_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v litellm_pw="$LITELLM_DB_PASSWORD" \
  -v gateway_pw="$PH_GATEWAY_DB_PASSWORD" \
  -v portal_pw="$PH_PORTAL_DB_PASSWORD" <<'SQL'
CREATE ROLE litellm LOGIN PASSWORD :'litellm_pw';
CREATE DATABASE litellm OWNER litellm;
CREATE ROLE ph_gateway LOGIN PASSWORD :'gateway_pw';
CREATE ROLE ph_portal LOGIN PASSWORD :'portal_pw';
CREATE DATABASE privacyhub;
REVOKE ALL ON DATABASE privacyhub FROM PUBLIC;
REVOKE ALL ON DATABASE litellm FROM PUBLIC;
GRANT CONNECT ON DATABASE privacyhub TO ph_gateway, ph_portal;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname privacyhub -f /schema/privacyhub.sql
