#!/bin/bash
# supervisord has no native "wait for another program to finish" primitive
# (unlike Compose's `condition: service_completed_successfully`), so backend/
# websocket/worker/scheduler all pipe through this before exec-ing the real command.
set -eu

MARKER=/home/frappe/frappe-bench/sites/.site_setup_complete
TIMEOUT="${SITE_SETUP_TIMEOUT:-600}"
elapsed=0

until [ -f "${MARKER}" ]; do
	if [ "${elapsed}" -ge "${TIMEOUT}" ]; then
		echo "[wait-for-site] timed out after ${TIMEOUT}s waiting for ${MARKER}" >&2
		exit 1
	fi
	sleep 2
	elapsed=$((elapsed + 2))
done

exec "$@"
