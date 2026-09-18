#!/bin/bash
# One-shot supervisord program: create the site (if missing), install
# frappe_theme and spice_next_core (if missing), always run bench migrate and
# clear-cache, then drop a marker file that wait-for-site.sh polls for.
# Adapted from the old multi-container docker/create-site/create-site.sh,
# plus the always-migrate/clear-cache steps — safe to automate here because
# this image is deliberately single-instance (no concurrent replicas to race
# on schema migration or cache invalidation).
set -eu

: "${SITE_NAME:?SITE_NAME must be set}"
: "${DB_HOST:?DB_HOST must be set}"
: "${DB_NAME:?DB_NAME must be set}"
: "${DB_USER:?DB_USER must be set}"
: "${DB_PASSWORD:?DB_PASSWORD must be set}"
: "${DB_SCHEMA:?DB_SCHEMA must be set}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
DB_PORT="${DB_PORT:-5432}"

cd /home/frappe/frappe-bench
MARKER="sites/.site_setup_complete"
rm -f "${MARKER}"

echo "[site-setup] waiting for Postgres and Redis to accept connections"
for i in $(seq 1 60); do
	pg_isready -h "${DB_HOST}" -p "${DB_PORT}" >/dev/null 2>&1 && break
	sleep 2
done
pg_isready -h "${DB_HOST}" -p "${DB_PORT}" >/dev/null 2>&1 || {
	echo "[site-setup] Postgres never became reachable — aborting" >&2
	exit 1
}
for i in $(seq 1 60); do
	redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1 && break
	sleep 2
done
redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1 || {
	echo "[site-setup] Redis never became reachable — aborting" >&2
	exit 1
}

if [ -d "sites/${SITE_NAME}" ]; then
	echo "[site-setup] site '${SITE_NAME}' already exists — skipping bench new-site"
else
	echo "[site-setup] creating site '${SITE_NAME}' against existing schema '${DB_SCHEMA}'"
	# --no-setup-db: DB_NAME/DB_SCHEMA already exist and are shared with another
	# service's tables, so this must NOT run Frappe's normal DROP DATABASE/CREATE
	# DATABASE/CREATE USER step -- --no-setup-db skips straight to creating only
	# Frappe's own (tab-prefixed) tables, using the already-granted DB_USER role.
	# db_schema itself comes from common_site_config.json (written by
	# entrypoint.sh from DB_SCHEMA) -- there's no --db-schema flag here; Frappe
	# reads it before this process's first connection. See
	# docs/superpowers/specs/2026-09-18-postgres-reuse-design.md in the uhis app repo.
	bench new-site "${SITE_NAME}" \
		--no-setup-db \
		--db-type postgres \
		--db-host "${DB_HOST}" \
		--db-port "${DB_PORT}" \
		--db-name "${DB_NAME}" \
		--db-user "${DB_USER}" \
		--db-password "${DB_PASSWORD}" \
		--admin-password "${ADMIN_PASSWORD}"
fi

# spice_next_core's required_apps = ["frappe_theme"] makes `install-app spice_next_core`
# install frappe_theme automatically on a genuinely fresh site — but this explicit,
# idempotent check also covers a site that had spice_next_core installed BEFORE
# frappe_theme became a dependency, where required_apps recursion never retroactively
# runs.
if bench --site "${SITE_NAME}" list-apps | grep -qx "frappe_theme"; then
	echo "[site-setup] frappe_theme already installed on '${SITE_NAME}'"
else
	echo "[site-setup] installing frappe_theme on '${SITE_NAME}'"
	bench --site "${SITE_NAME}" install-app frappe_theme
fi

if bench --site "${SITE_NAME}" list-apps | grep -qx "spice_next_core"; then
	echo "[site-setup] spice_next_core already installed on '${SITE_NAME}'"
else
	echo "[site-setup] installing spice_next_core on '${SITE_NAME}'"
	bench --site "${SITE_NAME}" install-app spice_next_core
fi

echo "[site-setup] running bench migrate"
bench --site "${SITE_NAME}" migrate

echo "[site-setup] clearing cache"
bench --site "${SITE_NAME}" clear-cache

echo "[site-setup] done"
touch "${MARKER}"
