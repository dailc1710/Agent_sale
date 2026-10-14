#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$POSTGRES_APP_PASSWORD" \
  --set=ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE hermes_app
  LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;

ALTER DATABASE hermes OWNER TO hermes_app;
GRANT ALL ON SCHEMA public TO hermes_app;
SQL
