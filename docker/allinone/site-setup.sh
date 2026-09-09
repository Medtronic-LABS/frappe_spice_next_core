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
: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"

cd /home/frappe/frappe-bench
MARKER="sites/.site_setup_complete"
rm -f "${MARKER}"

echo "[site-setup] waiting for MariaDB and Redis to accept connections"
for i in $(seq 1 60); do
	mysqladmin -h 127.0.0.1 -uroot -p"${DB_ROOT_PASSWORD}" ping >/dev/null 2>&1 && break
	sleep 2
done
mysqladmin -h 127.0.0.1 -uroot -p"${DB_ROOT_PASSWORD}" ping >/dev/null 2>&1 || {
	echo "[site-setup] MariaDB never became reachable — aborting" >&2
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
	echo "[site-setup] creating site '${SITE_NAME}'"
	# mariadb-uhis.cnf binds to 127.0.0.1 with skip-name-resolve off, so MariaDB
	# resolves any loopback TCP connection's apparent host as literally 'localhost'
	# (confirmed empirically: a user granted only '%' is rejected over this same TCP
	# connection that a 'localhost'-scoped user authenticates fine over) — scope the
	# new site's DB user to 'localhost' to match, not the more common '%' wildcard.
	bench new-site "${SITE_NAME}" \
		--db-host 127.0.0.1 \
		--db-root-password "${DB_ROOT_PASSWORD}" \
		--admin-password "${ADMIN_PASSWORD}" \
		--mariadb-user-host-login-scope=localhost
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
