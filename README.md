# AI Research Knowledge Platform

Runnable Phase 0/1 MVP for the AI 師生研究知識平台. It is a Django 5.2 modular monolith centered on governed research entities rather than a public PDF directory.

This delivery includes the repository foundation, RBAC-aware research catalog, Django admin, public research pages, protected PDF upload/download, keyword search, versioned read APIs, lifecycle and review governance, request tracing, migrations, tests, and deployment scaffolding. Semantic search, AI teacher matching, OCR/extraction, Graph-RAG, graph analytics, and news automation are intentionally deferred.

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
.venv/bin/python app/manage.py seed_demo_data
.venv/bin/python scripts/seed_research_fields.py
.venv/bin/python scripts/seed_research_methods.py
.venv/bin/python app/manage.py runserver
```

Open:

- Public site: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`
- Health check: `http://127.0.0.1:8000/healthz`

The first migration seeds the normalized `visitor`, `student`, `teacher`, and `admin` role records. Anonymous visitors do not need a database assignment.

## Local demo data

`seed_demo_data` is idempotent and is restricted to local, test, development, or demo environments by default. It creates clearly labelled, entirely fictitious showcase content:

- 5 demo teachers and 15 public research works, plus 1 student-only work
- 5 research fields, 5 methods, 5 awards, and 6 featured works
- one public PDF and one student-only PDF for authorization checks
- teacher and student role fixtures with unusable passwords by default

It contains no real student identity or real thesis content. To test the teacher-scoped Django Admin flow, set a teacher password locally after seeding:

```bash
.venv/bin/python app/manage.py changepassword demo_teacher_1
```

The teacher demo accounts are `demo_teacher_1` through `demo_teacher_5`. The `demo_student` fixture exists for backend permission checks, but Phase 1 has no student-facing login page yet. Do not reuse local demo passwords in another environment.

## Phase 1.5 governance and security

- Persisted `ResearchWork.status` changes go through a row-locked lifecycle service. Every legal transition records an immutable actor, reason, request ID, previous state, and resulting state. Historical imports may still create an initial non-draft state, but later changes cannot bypass the service.
- Teachers using Django Admin see and edit only their active linked `Professor`, advised `ResearchWork` records, and in-scope documents attached to those works. Advisor ownership and publication visibility cannot be reassigned by a teacher.
- A teacher upload is always `teacher` visibility. A `SourceDocument` file, parent work, and visibility are immutable after creation so evidence never silently points at different bytes or gains wider access; a future reviewed promotion workflow is intentionally deferred.
- `ReviewItem` governance fields are changed only by `decide_review_item()`. The service locks the item, validates the transition, appends an immutable `ReviewDecision`, and updates the item atomically.
- API 400, 403, 404, 405, and CSRF failures use `{error_code, message, details, request_id}`. Missing and unauthorized protected resources use the same non-disclosing 404 response.
- Every response has `X-Request-ID`. Upload responses, upload audit events, and lifecycle audits reuse the same sanitized or generated request ID.
- API and Django Admin uploads commit the `SourceDocument` and `AuditLog` together; a failed audit rolls back the row and removes the new blob.

## Tests and validation

```bash
.venv/bin/python app/manage.py check
.venv/bin/python app/manage.py makemigrations --check --dry-run
.venv/bin/python app/manage.py test
```

The suite covers model constraints, lifecycle and immutable transition audits, teacher object ownership, taxonomy aliases, private PDF validation/storage, typed graph edges, deterministic embedding fallback, governed review decisions, request IDs, structured API errors, transactional upload audits, public pages, and restricted-resource non-disclosure.

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
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml exec web python manage.py seed_demo_data
```

The local stack starts:

- `web`: Django + Gunicorn on `localhost:8000`
- `worker`: Celery on the same private application network
- `postgres`: PostgreSQL/pgvector, not published to the host/WAN
- `redis`: broker/result backend, not published to the host/WAN

If port 8000 is already in use, set `WEB_PORT=18000` in `deploy/.env` and open `http://localhost:18000` instead.

For a repeatable browser demo, start in the background, seed the fictitious catalog, then create an administrator interactively:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml up --build -d
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml exec web python manage.py seed_demo_data
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml exec web python manage.py createsuperuser
```

Open `/`, `/professors/`, `/search/`, and `/admin/` on the configured local port. To exercise teacher authorization in the browser, run `changepassword demo_teacher_1` inside the `web` container. The seed command preserves a password once you set it locally. Student-facing login/SSO is a later authentication integration.

Stop it with:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.local.yml down
```

Add `--volumes` only when you intentionally want to delete local database, Redis, upload, and static volumes.

## Core data loop

1. An administrator creates professors, students/authors, taxonomies, and research works in Django admin.
2. `WorkAdvisor`, `WorkAuthor`, `WorkField`, and `WorkMethod` create typed, reviewable relationships.
3. Only `published` works inside the caller's visibility tier are returned by public pages, keyword search, or APIs.
4. An administrator can upload to any work; a teacher can upload only to an active, visible work they advise, and the upload remains teacher-only. The validated PDF is stored under an opaque key in private storage.
5. Downloads pass through a backend permission check; Nginx must never map `/protected-media/` directly to the storage directory.
6. Upload and audit rows commit atomically and carry the response request ID.

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
- `POST /api/v1/research-works/{id}/documents` — administrator or linked teacher for an advised work
- `GET /api/v1/professors`
- `GET /api/v1/professors/{id}`
- `POST /api/v1/search/semantic` — explicit `501` Phase 2 stub
- `POST /api/v1/ai/teacher-matching` — explicit `501` Phase 4 stub

See `docs/api/openapi.yaml` for the current contract.

## Known Phase 1.5 limitations

- There is no student-facing login or institutional SSO route yet; student visibility is enforced and tested at the backend, but the current browser login surface is Django Admin for staff.
- Lifecycle transitions are available through the service and administrator actions, not a dedicated public REST transition endpoint.
- Teachers maintain existing advised works but cannot create works, change ownership, publish, or widen visibility; administrators perform those governed actions.
- Existing source documents cannot be promoted between visibility scopes in Phase 1.5; a reviewed document-release workflow belongs to the next governance increment.
- PDF malware scanning, OCR, parsing, embeddings, semantic search, RAG, AI matching, and news processing remain out of scope.

## Three-tier production baseline

- `fin-web`: public Nginx/TLS ingress and collected static assets
- `fin-app`: Django/Gunicorn, Celery, Redis, application logs, private uploads, and environment secrets
- `fin-db`: PostgreSQL/pgvector and backups

Only the web tier should receive public HTTP(S). Do not expose PostgreSQL, Redis, Gunicorn development ports, or SSH to the WAN. Concrete host addresses and account identifiers are deliberately absent from the repository. Production DNS, TLS, firewall, backup target, storage placement, and restore drill remain deployment decisions.

The Nginx example in `deploy/nginx/research-platform.conf` uses placeholders and deliberately has no direct protected-media alias.

## Phase boundary

Schemas and interfaces exist for `DocumentChunk`, vector documents/chunks, embeddings, retrieval/citations, prompt and AI request audit, AI extraction candidates, governed review decisions, and typed graph nodes/edges. Review transitions are active governance infrastructure; extraction, vector, RAG, and graph capabilities remain extension points rather than active AI features.

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
