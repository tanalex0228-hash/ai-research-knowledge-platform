# AI Research Knowledge Platform

Runnable Phase 0/1 MVP for the AI 師生研究知識平台. It is a Django 5.2 modular monolith centered on governed research entities rather than a public PDF directory.

This delivery includes the repository foundation, RBAC-aware research catalog, Django admin, public research pages, protected PDF upload/download, keyword search, versioned read APIs, migrations, tests, and deployment scaffolding. Semantic search, AI teacher matching, OCR/extraction, Graph-RAG, graph analytics, and news automation are intentionally deferred.

## Stack

- Python 3.13 and Django 5.2 LTS
- Django Templates, Bootstrap 5, and Chart.js
- PostgreSQL 16 with pgvector in Docker/production; SQLite for a zero-infrastructure local smoke run
- Redis and Celery (configured; extraction tasks remain explicit Phase 2 stubs)
- Gunicorn and Nginx deployment baseline

Python 3.13 is recommended because the selected Celery 5.x line officially supports it. The Docker image and CI use the same version.

## Quick start with SQLite

From the repository root:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python app/manage.py migrate
.venv/bin/python app/manage.py createsuperuser
.venv/bin/python scripts/seed_research_fields.py
.venv/bin/python scripts/seed_research_methods.py
.venv/bin/python app/manage.py runserver
```

Open:

- Public site: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`
- Health check: `http://127.0.0.1:8000/healthz`

The first migration seeds the normalized `visitor`, `student`, `teacher`, and `admin` role records. Anonymous visitors do not need a database assignment.

## Tests and validation

```bash
.venv/bin/python app/manage.py check
.venv/bin/python app/manage.py makemigrations --check --dry-run
.venv/bin/python app/manage.py test
```

The suite covers model constraints, role visibility, teacher/work relationships, taxonomy aliases, private PDF validation/storage, typed graph edges, deterministic embedding fallback, review state, public pages, JSON APIs, upload authorization, and restricted-resource non-disclosure.

For an already running service:

```bash
BASE_URL=http://127.0.0.1:8000 scripts/smoke_test.sh
```

## Local PostgreSQL, pgvector, Redis, and Celery

Use environment variables rather than editing settings:

```bash
cp .env.example .env
# Edit .env, set a PostgreSQL DATABASE_URL and safe local values, then export it.
set -a
. ./.env
set +a
.venv/bin/python app/manage.py migrate
cd app
../.venv/bin/celery -A config worker --loglevel=INFO
```

Run `scripts/create_pgvector_extension.sql` as a database migration/owner account before applying the RAG migration to an existing PostgreSQL database. The local Docker database does this automatically.

No external AI call is made in Phase 0/1. `AI_PROVIDER=disabled` is the safe default, and the deterministic local embedding adapter exists only for tests and interface validation.

## Docker Compose

```bash
cp deploy/env.example deploy/.env
# Replace the local-only placeholder values in deploy/.env.
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml up --build
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml exec web python manage.py createsuperuser
```

The local stack starts:

- `web`: Django + Gunicorn on `localhost:8000`
- `worker`: Celery on the same private application network
- `postgres`: PostgreSQL/pgvector, not published to the host/WAN
- `redis`: broker/result backend, not published to the host/WAN

If port 8000 is already in use, set `WEB_PORT=18000` in `deploy/.env` and open `http://localhost:18000` instead.

Stop it with:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml down
```

Add `--volumes` only when you intentionally want to delete local database, Redis, upload, and static volumes.

## Core data loop

1. An administrator creates professors, students/authors, taxonomies, and research works in Django admin.
2. `WorkAdvisor`, `WorkAuthor`, `WorkField`, and `WorkMethod` create typed, reviewable relationships.
3. Only `published` works inside the caller's visibility tier are returned by public pages, keyword search, or APIs.
4. An administrator can upload a validated PDF. It is stored under an opaque key in private storage.
5. Downloads pass through a backend permission check; Nginx must never map `/protected-media/` directly to the storage directory.
6. Upload actions write non-sensitive metadata to the append-only audit log.

Visibility tiers are cumulative:

| Role | Scopes |
|---|---|
| Visitor / anonymous | `public` |
| Student | `public`, `student` |
| Teacher | `public`, `student`, `teacher` |
| Administrator / superuser | all scopes |

`is_staff` alone does not widen research-data access. Student names and professor contact email are private by default.

## Implemented routes

Public HTML:

- `/` — research entry homepage
- `/professors/` and `/professors/{id}/` — professor exploration and drill-down
- `/research-works/{id}/` — published research detail
- `/search/` — permission-filtered keyword/structured search
- `/documents/{id}/download/` — permission-checked PDF stream
- `/healthz` and `/api/healthz` — liveness/database check

Versioned JSON API:

- `GET /api/v1/research-works`
- `GET /api/v1/research-works/{id}`
- `POST /api/v1/research-works/{id}/documents` — administrator only
- `GET /api/v1/professors`
- `GET /api/v1/professors/{id}`
- `POST /api/v1/search/semantic` — explicit `501` Phase 2 stub
- `POST /api/v1/ai/teacher-matching` — explicit `501` Phase 4 stub

See `docs/api/openapi.yaml` for the current contract.

## Three-tier production baseline

- `fin-web`: public Nginx/TLS ingress and collected static assets
- `fin-app`: Django/Gunicorn, Celery, Redis, application logs, private uploads, and environment secrets
- `fin-db`: PostgreSQL/pgvector and backups

Only the web tier should receive public HTTP(S). Do not expose PostgreSQL, Redis, Gunicorn development ports, or SSH to the WAN. Concrete host addresses and account identifiers are deliberately absent from the repository. Production DNS, TLS, firewall, backup target, storage placement, and restore drill remain deployment decisions.

The Nginx example in `deploy/nginx/research-platform.conf` uses placeholders and deliberately has no direct protected-media alias.

## Phase boundary

Schemas and interfaces exist for `DocumentChunk`, vector documents/chunks, embeddings, retrieval/citations, prompt and AI request audit, AI extraction candidates, review decisions, and typed graph nodes/edges. They are extension points—not active AI features.

The following remain out of scope for this delivery:

- PDF parsing, OCR, automatic chunking, embeddings, and AI extraction
- Semantic/vector search and RAG response generation
- AI teacher matching and research-navigation sessions
- Graph traversal/analytics/visualization
- News ingestion and research-idea generation

Deferred APIs and tasks fail closed with `not implemented`; they do not silently call an LLM or retrieve unrestricted content. See `docs/architecture/phase-0-1-boundaries.md`.

## Secrets and licensing

Never commit `.env`, database passwords, router/Wi-Fi credentials, server credentials, or AI keys. Production secrets belong in the application host's secret environment and must be rotated according to the deployment policy.

No open-source license has been selected for this new project. Do not redistribute it until the owner chooses and adds a license.
