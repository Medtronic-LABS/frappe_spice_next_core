#!/bin/bash
# Container entrypoint (runs as root). One job before handing off to supervisord:
# every boot, re-materialize sites/ config from the build-time seed, since mounting
# a volume under sites/ shadows whatever was baked into the image there.
#
# common_site_config.json is the one exception -- it's written fresh here, not
# copied from the seed, because db_host/db_port/db_schema are deployment-specific
# values for an existing, already-populated Postgres database/schema (shared with
# another service) that don't exist at image-build time. See
# docs/superpowers/specs/2026-09-18-postgres-reuse-design.md in the uhis app repo.
set -eu

: "${DB_HOST:?DB_HOST must be set}"
: "${DB_SCHEMA:?DB_SCHEMA must be set}"
DB_PORT="${DB_PORT:-5432}"

echo "[entrypoint] re-materializing sites/ config from build-time seed"
mkdir -p /home/frappe/frappe-bench/sites
cp -f /home/frappe/sites-seed/apps.txt  /home/frappe/frappe-bench/sites/apps.txt
cp -f /home/frappe/sites-seed/apps.json /home/frappe/frappe-bench/sites/apps.json
# Always replace, never "copy only if missing" — assets are 100% code-derived (never
# user data) and their content hashes change on every rebuild. A long-lived
# bench-sites volume that skipped this on later boots would keep serving a stale
# assets.json referencing filenames the new image no longer has, breaking the site
# after every deploy that touches frontend assets.
rm -rf /home/frappe/frappe-bench/sites/assets
cp -r /home/frappe/sites-seed/assets /home/frappe/frappe-bench/sites/assets

echo "[entrypoint] writing common_site_config.json from runtime DB_* env vars"
cat > /home/frappe/frappe-bench/sites/common_site_config.json <<JSON
{
  "db_type": "postgres",
  "db_host": "${DB_HOST}",
  "db_port": ${DB_PORT},
  "db_schema": "${DB_SCHEMA}",
  "redis_cache": "redis://127.0.0.1:6379/0",
  "redis_queue": "redis://127.0.0.1:6379/1",
  "redis_socketio": "redis://127.0.0.1:6379/2",
  "socketio_port": 9000
}
JSON

chown -R frappe:frappe /home/frappe/frappe-bench/sites

exec supervisord -c /etc/supervisor/supervisord.conf
