#!/bin/sh
set -eu

app_password="$(cat /run/secrets/postgres_app_password)"
[ -n "$app_password" ] || {
  printf 'postgres_app_password is empty\n' >&2
  exit 1
}

# The official image runs this once, while initializing an empty data volume.
# LiteLLM gets a database owner role, never the cluster bootstrap superuser.
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=app_password="$app_password" <<'SQL'
CREATE ROLE litellm WITH LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
CREATE DATABASE litellm OWNER litellm;
REVOKE CONNECT ON DATABASE litellm FROM PUBLIC;
GRANT CONNECT ON DATABASE litellm TO litellm;
SQL

unset app_password
