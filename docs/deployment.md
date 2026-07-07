# Deployment

This repo owns the **image** — the Dockerfile, process supervision, and the CI/CD
pipeline that builds and publishes it. It does not own the production compose file,
nginx config, or the shared-gateway integration with the legacy UHIS platform; that
lives in `frappe-uhis-next` (the ops repo, Bitbucket `MDTLabs/platform-setup`), whose
`docs/deployment/production.md` and `STEPS.md` are the actual runbook. Nothing below
duplicates that content — read this for what ships from *this* repo, read that for how
it's actually deployed.

## Architecture

`docker/allinone/Dockerfile` builds a single all-in-one image — bench (Frappe +
`uhis_next_core` + `frappe_theme`), MariaDB, and Redis all running as processes
supervised by `supervisord` inside one container. Published to GHCR as
`ghcr.io/medtronic-labs/frappe_uhis_next_core`.

This replaced an earlier design that split those roles across nine separate
containers. That design assumed a `frappe/bench:latest` container would already have
a working bench in its shared volume — it never did (`frappe/bench:latest` ships no
pre-built bench at all). The all-in-one image avoids that class of bug entirely:
`bench init`, both apps' installation, and `bench build` all happen at **image build
time**, not at container start.

Only two things persist across restarts/deploys, via named volumes owned by the
compose file in the ops repo:

- `/var/lib/mysql` — MariaDB's data directory
- `/home/frappe/frappe-bench/sites` — site config, DB credentials, and uploaded
  files (`sites/<site>/public/files`, `.../private/files`)

Everything else — app code, the Python venv, built JS/CSS — is baked into the image.
A new image build is how code ships, not a volume.

### What runs on every container boot

`entrypoint.sh` (root) then `site-setup.sh` (frappe), in order:

1. First boot only (empty `/var/lib/mysql`): `mariadb-install-db
   --auth-root-authentication-method=normal` + set the root password over a
   temporary, network-isolated `mariadbd`. Skipped on subsequent boots.
2. Every boot: re-materialize `common_site_config.json`/`apps.txt`/`apps.json` and
   fully refresh `sites/assets/` from what was baked into the image. These are all
   code-derived, never user data — mounting the `sites` volume shadows whatever the
   image baked in there, so this re-seeds it. `sites/assets/` specifically must be
   refreshed on **every** boot, not just when missing — its filenames are
   content-hashed and change on every rebuild; an old volume that skipped this would
   keep serving a stale `assets.json` referencing files the new image doesn't have.
3. `site-setup.sh` (a one-shot supervised program): create the site if it doesn't
   exist, install `frappe_theme` and `uhis_next_core` if not already installed
   (`uhis_next_core`'s `hooks.py` declares `required_apps = ["frappe_theme"]`, so a
   fresh install pulls it in automatically — the explicit check here additionally
   covers upgrading a site that had `uhis_next_core` installed *before* that
   dependency existed), then **always** run `bench migrate`. Automating migrate on
   every boot is safe specifically because this image is deliberately
   single-instance — there are no concurrent replicas to race on schema migration.
4. `backend`/`websocket`/`worker`/`scheduler` each wait on a marker file
   (`wait-for-site.sh`) that `site-setup.sh` touches once done, since supervisord has
   no native equivalent to Compose's `service_completed_successfully`.

Nothing here ever touches `sites/<site>/` itself — that's where the actual database
content and uploaded files live, and it persists purely because it's part of the same
volume, untouched by any of these scripts.

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
docker build -f docker/allinone/Dockerfile -t uhis-next-core:local .

docker volume create uhis-next-test-mariadb
docker volume create uhis-next-test-sites

docker run -d --name uhis-next-core-test \
  -e SITE_NAME=test.localhost \
  -e DB_ROOT_PASSWORD=<pick one> \
  -e ADMIN_PASSWORD=<pick one> \
  -v uhis-next-test-mariadb:/var/lib/mysql \
  -v uhis-next-test-sites:/home/frappe/frappe-bench/sites \
  -p 8000:8000 -p 9000:9000 \
  uhis-next-core:local

# Wait for first-boot site creation (new-site + install-app + migrate) to finish:
docker exec uhis-next-core-test test -f /home/frappe/frappe-bench/sites/.site_setup_complete

# Every long-running program should be RUNNING, site-setup EXITED(0) — not FATAL:
docker exec uhis-next-core-test supervisorctl -c /etc/supervisor/supervisord.conf status

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
| `DB_ROOT_PASSWORD` | Yes | Sets MariaDB's root password on first boot; used by `site-setup.sh` |
| `ADMIN_PASSWORD` | Yes | Becomes the new site's Administrator password |

No insecure defaults — the container will not start without all three set.

## CI/CD

`.github/workflows/docker-publish.yml`:

- **`build-and-push`** — triggers on push to `main`, a `v*.*.*` tag, or manual
  dispatch. Builds `docker/allinone/Dockerfile`, tags it (`latest` on `main`,
  short-SHA always, semver on a version tag), pushes to
  `ghcr.io/medtronic-labs/frappe_uhis_next_core`.
- **`deploy`** — gated to `main` only (a version-tag push publishes an image without
  also redeploying whatever `main` most recently put live). SSHes into the
  production server (`appleboy/ssh-action`) and runs `docker compose pull backend &&
  up -d backend` against the ops repo's compose file, then polls the site's
  `/api/method/ping` through the proxy and fails the workflow if it doesn't come up
  healthy. No automated rollback.

Required repo secrets for the `deploy` job: `DEPLOY_HOST`, `DEPLOY_USER`,
`DEPLOY_SSH_KEY`, `DEPLOY_PORT` (optional, defaults to 22). GHCR auth on the
production server is a one-time `docker login ghcr.io` with a `read:packages`-scoped
PAT — not something this workflow repeats on every deploy.
