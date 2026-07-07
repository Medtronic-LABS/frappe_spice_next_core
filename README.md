# UHIS Next Core

[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)](https://www.python.org/)
[![Frappe](https://img.shields.io/badge/frappe-v15-blue)](https://frappeframework.com/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

**Multi-country public health platform — server of record and sync API provider.**

A [Frappe](https://frappeframework.com) app built by [Medtronic Labs](https://medtroniclabs.org) that powers the UHIS (Universal Health Information System) backend, designed for community health worker (CHW) programmes at national scale.

---

## What it does

- **Clinical data model** — DocTypes for Patient, Household, Case (episode of care), Encounter, Observation, Condition, Referral, Task, Programme, Care Team, Facility, and a multi-level Geography hierarchy.
- **Offline-first CHW sync** — purpose-built `sync.push` / `sync.pull` API for an Android CHW client; cursor-based, idempotent on client op-id, and server-side catchment-scoped.
- **Dynamic form config** — metadata-driven clinical forms (`Form DocType`, `Clinical Question`) consumed by the mobile app; conditional navigation defined as `field / operator / value` triples evaluated identically on device and server.
- **FHIR R4 egress** — Patient, Encounter, Observation, Condition, EpisodeOfCare, ServiceRequest, and Group (Household) resources produced at the API boundary via a mapper; nothing is stored as FHIR internally.
- **AI clinical narratives** — on-demand and stale-on-change summaries for Case, Patient, Household, and Facility, streamed from a local or cloud LLM via an OpenAI-compatible endpoint; includes a follow-up chat interface.
- **Risk scoring** — automated Low / Moderate / High risk classification recomputed on every new Observation, with append-only guardrails on Encounters and Observations.

---

## Requirements

| Dependency | Version |
|---|---|
| [Frappe Framework](https://github.com/frappe/frappe) | v15 (`version-15` branch) |
| Python | ≥ 3.10 |
| MariaDB | 10.6+ |
| Node.js | 18+ (for `bench build`) |

---

## Installation

```bash
# From inside your bench directory
bench get-app https://github.com/Medtronic-LABS/frappe_uhis_next_core.git

# Install on a site
bench --site <your-site> install-app uhis_next_core
bench --site <your-site> migrate
```

> **Note:** An OpenAI-compatible LLM endpoint (local via [Ollama](https://ollama.com) or cloud) is required for AI narrative features. Configure it in **UHIS Settings** after installation.

---

## Development setup

```bash
# Clone into your bench apps directory
cd frappe-bench/apps
git clone https://github.com/Medtronic-LABS/frappe_uhis_next_core.git uhis_next_core

# Install the app in editable mode
../env/bin/pip install -e uhis_next_core

# Install pre-commit hooks
pip install pre-commit
cd uhis_next_core
pre-commit install
```

### Linting

```bash
# Run all pre-commit hooks against all files
pre-commit run --all-files

# Run ruff directly
ruff check uhis_next_core/
ruff format uhis_next_core/
```

---

## Running tests

```bash
bench --site <your-site> run-tests --app uhis_next_core
```

---

## Architecture overview

```
uhis_next_core/
├── hooks.py              # App hooks — CSS/JS includes, doc_events, scheduler
├── hooks_impl.py         # advance_sync_seq, validate_clinical_fields
├── api/                  # Whitelisted API endpoints
│   ├── sync.py           # sync.push / sync.pull
│   ├── fhir.py           # FHIR R4 read endpoints
│   ├── form_config.py    # Form metadata for mobile
│   ├── case_summary.py   # Case health summary
│   ├── patient_summary.py
│   └── household_summary.py
├── fhir/                 # FHIR egress (push) + ingress (pull)
├── ai/                   # LLM narrative generation and chat
├── risk/                 # Risk scoring engine
├── overrides/            # append_only_guard for Observation/Encounter
└── uhis_next_core/
    └── doctype/          # 22 DocType definitions
```

---

## Deployment

This app ships as a single all-in-one production image (bench + MariaDB + Redis,
built from `docker/allinone/Dockerfile` and published to GHCR by
`.github/workflows/docker-publish.yml`) — see [`docs/deployment.md`](docs/deployment.md)
for the image's architecture, how to build/test it locally, and the CI/CD pipeline.

---

## License

Copyright (C) 2024 Medtronic Labs

This program is free software: you can redistribute it and/or modify it under the terms of the
[GNU General Public License v3.0](LICENSE) as published by the Free Software Foundation.
