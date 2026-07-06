#!/bin/bash
# Container entrypoint (runs as root). Two jobs before handing off to supervisord:
#   1. First boot only: initialize MariaDB's data directory and set its root
#      password (mariadb-install-db defaults root to unix-socket-only auth, which
#      the `frappe` OS user can't use over TCP with a password — hence the
#      --auth-root-authentication-method=normal flag and the explicit ALTER USER).
#   2. Every boot: re-materialize common_site_config.json/apps.txt/apps.json/assets
#      from the build-time seed, since mounting a volume under sites/ shadows
#      whatever was baked into the image there.
set -eu

: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set}"

if [ ! -d /var/lib/mysql/mysql ]; then
	echo "[entrypoint] initializing MariaDB data directory (first boot)"
	mariadb-install-db \
		--auth-root-authentication-method=normal \
		--user=mysql \
		--datadir=/var/lib/mysql \
		>/var/log/supervisor/mariadb-install-db.log 2>&1

	echo "[entrypoint] starting temporary mariadbd to set root password"
	gosu mysql /usr/sbin/mariadbd --skip-networking --socket=/run/mysqld/mysqld.sock \
		--datadir=/var/lib/mysql &
	tmp_pid=$!

	for i in $(seq 1 30); do
		mysqladmin --socket=/run/mysqld/mysqld.sock ping >/dev/null 2>&1 && break
		sleep 1
	done

	mysql --socket=/run/mysqld/mysqld.sock -u root <<-SQL
		ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_ROOT_PASSWORD}';
		FLUSH PRIVILEGES;
	SQL

	mysqladmin --socket=/run/mysqld/mysqld.sock -u root -p"${DB_ROOT_PASSWORD}" shutdown
	wait "${tmp_pid}" 2>/dev/null || true
	echo "[entrypoint] MariaDB root password set"
fi

echo "[entrypoint] re-materializing sites/ config from build-time seed"
mkdir -p /home/frappe/frappe-bench/sites
cp -f /home/frappe/sites-seed/common_site_config.json /home/frappe/frappe-bench/sites/common_site_config.json
cp -f /home/frappe/sites-seed/apps.txt               /home/frappe/frappe-bench/sites/apps.txt
cp -f /home/frappe/sites-seed/apps.json              /home/frappe/frappe-bench/sites/apps.json
# Always replace, never "copy only if missing" — assets are 100% code-derived (never
# user data) and their content hashes change on every rebuild. A long-lived
# bench-sites volume that skipped this on later boots would keep serving a stale
# assets.json referencing filenames the new image no longer has, breaking the site
# after every deploy that touches frontend assets.
rm -rf /home/frappe/frappe-bench/sites/assets
cp -r /home/frappe/sites-seed/assets /home/frappe/frappe-bench/sites/assets
chown -R frappe:frappe /home/frappe/frappe-bench/sites

exec supervisord -c /etc/supervisor/supervisord.conf
