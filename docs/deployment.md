# Deployment

This repo owns the **image** — the Dockerfile and the process supervision it runs
under. It does not own the CI/CD pipeline that builds, publishes, and deploys it
(that lives in `uhis`, Medtronic-LABS/frappe_uhis — see its own
`.github/workflows/docker-publish.yml`), nor the production compose file, nginx
config, or the shared-gateway integration with the legacy UHIS platform; that lives in
`frappe-uhis-next` (the ops repo, Bitbucket `MDTLabs/platform-setup`), whose
`docs/deployment/production.md` and `STEPS.md` are the actual runbook. Nothing below
duplicates that content — read this for what ships from *this* repo, read those for
how it's actually built and deployed.

## Architecture

`docker/allinone/Dockerfile` builds a single all-in-one image — bench (Frappe +
`spice_next_core` + `frappe_theme`) and Redis running as processes supervised by
`supervisord` inside one container. Published to GHCR as
`ghcr.io/medtronic-labs/frappe_spice_next_core`.

Postgres is **not** bundled in this image. The site connects to an existing,
already-populated Postgres database/schema shared with another service — see
`docs/superpowers/specs/2026-09-18-postgres-reuse-design.md` in the `uhis` app repo
(Medtronic-LABS/frappe_uhis) for the full design and why (`bench new-site
--no-setup-db` against an existing schema, not a dedicated database Frappe creates
itself).

This replaced an earlier design that split those roles across nine separate
containers. That design assumed a `frappe/bench:latest` container would already have
a working bench in its shared volume — it never did (`frappe/bench:latest` ships no
pre-built bench at all). The all-in-one image avoids that class of bug entirely:
`bench init`, both apps' installation, and `bench build` all happen at **image build
time**, not at container start.

Only one thing persists in this container across restarts/deploys, via a named
volume owned by the compose file in the ops repo:

- `/home/frappe/frappe-bench/sites` — site config, DB credentials, and uploaded
  files (`sites/<site>/public/files`, `.../private/files`)

Actual database content lives outside this container entirely, in the existing
Postgres server/schema this image connects to over the network.

Everything else — app code, the Python venv, built JS/CSS — is baked into the image.
A new image build is how code ships, not a volume.

### What runs on every container boot

`entrypoint.sh` (root) then `site-setup.sh` (frappe), in order:

1. Every boot: re-materialize `apps.txt`/`apps.json` and fully refresh
   `sites/assets/` from what was baked into the image. These are all code-derived,
   never user data — mounting the `sites` volume shadows whatever the image baked
   in there, so this re-seeds it. `sites/assets/` specifically must be refreshed on
   **every** boot, not just when missing — its filenames are content-hashed and
   change on every rebuild; an old volume that skipped this would keep serving a
   stale `assets.json` referencing files the new image doesn't have.
   `common_site_config.json` is written fresh here too (not from a build-time
   seed), from `DB_HOST`/`DB_PORT`/`DB_SCHEMA` env vars — those are
   deployment-specific and don't exist at image-build time.
2. `site-setup.sh` (a one-shot supervised program): create the site if it doesn't
   exist (`bench new-site --no-setup-db` against the already-existing Postgres
   database/schema — it does not create or drop that database), install
   `frappe_theme` and `spice_next_core` if not already installed
   (`spice_next_core`'s `hooks.py` declares `required_apps = ["frappe_theme"]`, so a
   fresh install pulls it in automatically — the explicit check here additionally
   covers upgrading a site that had `spice_next_core` installed *before* that
   dependency existed), then **always** run `bench migrate`. Automating migrate on
   every boot is safe specifically because this image is deliberately
   single-instance — there are no concurrent replicas to race on schema migration.
3. `backend`/`websocket`/`worker`/`scheduler` each wait on a marker file
   (`wait-for-site.sh`) that `site-setup.sh` touches once done, since supervisord has
   no native equivalent to Compose's `service_completed_successfully`.

Nothing here ever touches the other service's existing tables in that schema —
`--no-setup-db` skips Frappe's normal database create/drop step entirely, and
`bench install-app` only ever creates Frappe's own (`tab`-prefixed) tables.

### Why gunicorn needs `wsgi.py`

`docker/allinone/run-backend.sh` runs gunicorn against `wsgi:application`
(`docker/allinone/wsgi.py`), not `frappe.app:application` directly. The bare
`frappe.app` WSGI callable skips the static-file-serving middleware that `bench
serve` (Werkzeug's dev server) adds via `application_with_statics()` — under plain
gunicorn, every `/assets` and `/files` request would 404 even though the files exist
on disk. `wsgi.py` applies that same wrapping.

## Building and testing locally

```bash
# From the repo root
docker build -f docker/allinone/Dockerfile -t spice-next-core:local .

# A throwaway local Postgres, standing in for the real existing database/schema.
# The schema + a dummy table simulate the other service's pre-existing tables --
# --no-setup-db must leave that table untouched.
docker network create spice-test-net
docker run -d --name spice-test-pg --network spice-test-net \
  -e POSTGRES_USER=spice -e POSTGRES_PASSWORD=<pick one> -e POSTGRES_DB=shared_db \
  postgres:16
docker exec spice-test-pg psql -U spice -d shared_db \
  -c 'CREATE SCHEMA IF NOT EXISTS spice_next_core; CREATE TABLE IF NOT EXISTS spice_next_core.other_service_table (id int);'

docker volume create uhis-next-test-sites

docker run -d --name spice-next-core-test --network spice-test-net \
  -e SITE_NAME=test.localhost \
  -e DB_HOST=spice-test-pg \
  -e DB_NAME=shared_db \
  -e DB_USER=spice \
  -e DB_PASSWORD=<same as POSTGRES_PASSWORD above> \
  -e DB_SCHEMA=spice_next_core \
  -e ADMIN_PASSWORD=<pick one> \
  -v uhis-next-test-sites:/home/frappe/frappe-bench/sites \
  -p 8000:8000 -p 9000:9000 \
  spice-next-core:local

# Wait for first-boot site creation (new-site + install-app + migrate) to finish:
docker exec spice-next-core-test test -f /home/frappe/frappe-bench/sites/.site_setup_complete

# Confirm the other service's table is still there, untouched:
docker exec spice-test-pg psql -U spice -d shared_db -c '\dt spice_next_core.*'

# Every long-running program should be RUNNING, site-setup EXITED(0) — not FATAL:
docker exec spice-next-core-test supervisorctl -c /etc/supervisor/supervisord.conf status

# Direct check (bypassing nginx, so the site-name header must be set explicitly):
curl -H "X-Frappe-Site-Name: test.localhost" http://localhost:8000/api/method/ping
```

To test through the same nginx sidecar and gateway wiring production uses, bring up
the ops repo's `docker-compose.prod.yml` instead (see its README/runbook) — that's
the only place the full stack (image + nginx + shared network) is assembled.

### Environment variables the image requires

| Variable | Required | Purpose |
|---|---|---|
| `SITE_NAME` | Yes | The bench site name Frappe resolves requests against |
| `DB_HOST` | Yes | Host of the existing Postgres server |
| `DB_PORT` | No (default `5432`) | Port of the existing Postgres server |
| `DB_NAME` | Yes | The existing, already-populated database name |
| `DB_USER` | Yes | An existing role, already granted `CREATE`/`USAGE` on `DB_SCHEMA` — this image never creates or drops a role |
| `DB_PASSWORD` | Yes | Password for `DB_USER` |
| `DB_SCHEMA` | Yes | The existing schema Frappe's tables live in, alongside the other service's tables already there |
| `ADMIN_PASSWORD` | Yes | Becomes the new site's Administrator password |

No insecure defaults for any required variable — the container will not start
without them set. There is no `DB_ROOT_PASSWORD` — nothing here ever needs
superuser access to Postgres, since `--no-setup-db` never creates or drops a
database or role (see
`docs/superpowers/specs/2026-09-18-postgres-reuse-design.md` in the `uhis` app
repo).

## CI/CD

This repo has **no GitHub Actions workflow of its own** — `spice_next_core` is a
shared core package consumed by more than one deployment, so build/publish/deploy is
owned by whichever app assembles a given deployment, not by this repo. For the
uhis-next production deployment, that's `uhis` (Medtronic-LABS/frappe_uhis,
`.github/workflows/docker-publish.yml`): it checks out this repo at a pinned ref
alongside `frappe_theme`, `shukhee_integration`, and `leapwell_telemetry`, builds its
own all-in-one image, publishes it to `ghcr.io/medtronic-labs/frappe_uhis`, and
deploys it.

To sanity-check *this* repo's own image in isolation, with no deploy step involved,
use the local build/test commands above.
