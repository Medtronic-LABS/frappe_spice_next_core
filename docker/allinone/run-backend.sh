#!/bin/bash
# supervisord does not invoke `command=` through a shell, so the worker-count
# arithmetic (2×vCPU+1) that used to be inlined via `sh -c "..."` in
# docker-compose.prod.yml needs its own script to expand $(nproc) first.
set -eu
cd /home/frappe/frappe-bench

WORKERS=$(( $(nproc) * 2 + 1 ))

# Full path, not just `gunicorn` — it lives in the bench's own venv (env/bin), which
# is on PATH inside an interactive `bench` shell but not for a script supervisord
# execs directly.
#
# wsgi:application (not frappe.app:application) — the bare frappe.app callable skips
# the static-asset-serving middleware that `bench serve` normally adds, so /assets and
# /files 404 under plain gunicorn. --pythonpath makes the bench-root wsgi.py wrapper
# importable even though --chdir points at sites/ (required for frappe's own site
# resolution, which is relative to cwd).
exec /home/frappe/frappe-bench/env/bin/gunicorn --chdir /home/frappe/frappe-bench/sites \
	--pythonpath /home/frappe/frappe-bench --bind 0.0.0.0:8000 \
	--timeout 120 --worker-class gthread --threads 2 \
	--workers "${WORKERS}" wsgi:application
