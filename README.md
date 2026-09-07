# AI Research Knowledge Platform

Runnable Phase 0/1 MVP with Phase 2 ingestion foundation and Phase 3/4 baseline APIs for the AI 師生研究知識平台. It is a Django 5.2 modular monolith centered on governed research entities rather than a public PDF directory.

This delivery includes the repository foundation, RBAC-aware research catalog, Django admin, public research pages, protected PDF upload/download, keyword search, versioned APIs, lifecycle and review governance, request tracing, OCR/page-aware ingestion foundation, review-item promotion, deterministic metadata extraction fallback, knowledge-graph synchronization, local embedding-backed semantic retrieval, floating research navigator persistence, deterministic teacher matching, migrations, tests, and deployment scaffolding. Graph-RAG answer generation, advanced graph analytics, and news automation are intentionally deferred.

## Stack

- Python 3.13 and Django 5.2 LTS
- Django Templates, Bootstrap 5, and Chart.js
- PostgreSQL 16 with pgvector in Docker/production; SQLite for a zero-infrastructure local smoke run
- Redis and Celery for document ingestion/background processing
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
- A teacher upload is always `teacher` visibility. A `SourceDocument` file, parent work, and uploader are immutable after creation so evidence never silently points at different bytes. Administrators may change document visibility individually or in bulk; this controls PDF download/preview only and does not modify the research work, file bytes, chunks, or extraction results.
- `ReviewItem` governance fields are changed only by `decide_review_item()`. The service locks the item, validates the transition, appends an immutable `ReviewDecision`, and updates the isolated review record atomically. AI analysis is never promoted into ResearchWork, taxonomy, people, graph, or lifecycle data.
- API 400, 403, 404, 405, and CSRF failures use `{error_code, message, details, request_id}`. Missing and unauthorized protected resources use the same non-disclosing 404 response.
- Every response has `X-Request-ID`. Upload responses, upload audit events, and lifecycle audits reuse the same sanitized or generated request ID.
- API and Django Admin uploads commit the `SourceDocument` and `AuditLog` together; a failed audit rolls back the row and removes the new blob.

## Tests and validation

```bash
.venv/bin/python app/manage.py check
.venv/bin/python app/manage.py makemigrations --check --dry-run
.venv/bin/python app/manage.py test
```

### 固定格式學生名冊匯入

`系統名單.xlsx` 的學生帳號可透過後端管理指令匯入。它只讀取 `金企二甲`、`金企二乙`、`金企三甲`、`金企三乙`、`金企四甲`、`金企四乙`，絕不讀取或修改 `教師名單`。匯入會建立 StudentRoster、User、UserProfile 與既有的 `student` 角色；不會建立 Professor、修改教師帳號或重設既有帳號密碼。

先執行預演。預演會驗證固定欄位、student 身份／角色、學號與輔仁雲端信箱的一致性，以及帳號、學號、使用者名稱的唯一性，且不寫入資料庫：

```bash
python app/manage.py import_system_roster /path/to/系統名單.xlsx
```

預演成功後，才以明確的 `--apply` 建立資料。新帳號的使用者名稱為「姓名」，密碼只從 Excel 讀入並安全雜湊儲存，絕不寫入命令輸出或稽核紀錄：

```bash
python app/manage.py import_system_roster /path/to/系統名單.xlsx --apply --actor admin@example.edu.tw
```

同名學生的內部使用者名稱會自動採用其已驗證的輔仁雲端信箱；Profile 與介面仍只顯示本名。重複學號或信箱會停止整批匯入且不寫入任何資料。若管理員已確認某一列為重複／撤銷資料，可明確排除該列：

```bash
python app/manage.py import_system_roster /path/to/系統名單.xlsx --exclude-row 金企三乙!64 --apply --actor admin@example.edu.tw
```

匯入本身不授予專題上傳資格。

研究成果的「作者」在 Django Admin 中可直接搜尋並選擇已啟用、且具有 `student` 角色的使用者。系統會自動建立或連結該使用者的隱私保護研究作者身分；公開頁面的姓名可見性仍依研究學生設定處理。

### 研究學生作者可見性

在 Django Admin 的「研究目錄 → 研究學生」可勾選多位作者後，從「動作」選擇「批量設定可見性範圍」。選擇「學生與教職員」後，已登入的 student、teacher、admin 可在研究成果頁看見作者姓名；訪客仍維持匿名。未來從已登入學生帳號建立的研究學生作者，預設亦為「學生與教職員」。

### 使用者批量授予權限群組

平台管理員可在 Django Admin 的「帳號與角色 → 使用者」勾選帳號後，從「動作」選擇「授予權限群組」。確認頁會列出所有目前建立的 Django 權限群組（例如 `student`、`teacher`、`USER manager(root)`）；日後新增群組會自動出現在清單中。此操作只會新增群組、不會移除既有群組，並會寫入稽核紀錄。

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

No external AI call is required for the local demo path. `DOCUMENT_INTELLIGENCE_PROVIDER=deterministic` and `AI_PROVIDER=disabled` are safe defaults; the deterministic metadata extractor and local embedding adapter keep the prototype testable without model credentials.

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
- `/fields/{slug}/` — field page with related works and professors
- `/methods/{slug}/` — method page with related works and professors
- `/research-works/{id}/` — published research detail
- `/search/` — permission-filtered keyword/structured search
- `/documents/{id}/download/` — permission-checked PDF stream
- `/healthz` and `/api/healthz` — liveness/database check

Versioned JSON API:

- `GET /api/v1/research-works`
- `GET /api/v1/research-works/{id}`
- `POST /api/v1/research-works/{id}/documents` — administrator or linked teacher for an advised work
- `POST /api/v1/ingestion-jobs` — administrator-only queue/requeue document ingestion
- `GET /api/v1/ingestion-jobs/{id}` — administrator-only ingestion status
- `GET /api/v1/professors`
- `GET /api/v1/professors/{id}`
- `GET /api/v1/fields/{id}`
- `GET /api/v1/methods/{id}`
- `POST /api/v1/search/semantic` — permission-filtered semantic retrieval over available vector chunks
- `GET /api/v1/ai/assistant/session` — current active floating Research Navigator session
- `POST /api/v1/ai/assistant/messages` — send a page-aware navigator message
- `POST /api/v1/ai/assistant/end` — end the current navigator session
- `POST /api/v1/ai/teacher-matching` — deterministic evidence-based teacher matching
- `POST /api/v1/ai/research-navigation/sessions` — create or resume an isolated navigator session
- `POST /api/v1/ai/research-navigation/sessions/{id}/messages` — send a message inside a specific isolated session
- `GET /api/v1/graph/nodes/{id}/neighbors` — permission-filtered approved graph neighbors

See `docs/api/openapi.yaml` for the current contract.

## Floating AI Research Navigator

Every public page includes a bottom-right Research Navigator button. Opening and
closing the drawer changes only the browser UI; the active conversation remains
available until the user presses `結束對話`. Messages are persisted in
`AssistantSession` and `AssistantMessage`, isolated by authenticated user or by
the anonymous Django browser session.

The navigator is intentionally database-first. It collects the current page type
and object ID from the browser, then re-queries visible research works,
professors, fields, and methods on the server before constructing the prompt.
It does not read PDFs, regenerate summaries, use vector search, or promote any
AI output into approved metadata.

## Known limitations

- Unified `/auth/` student/teacher/admin login and StudentRoster verification are not complete in this branch; student visibility is enforced and tested at the backend, but the current browser login surface is Django Admin for staff.
- Lifecycle transitions are available through the service and administrator actions, not a dedicated public REST transition endpoint.
- Teachers maintain existing advised works but cannot create works, change ownership, publish, or widen visibility; administrators perform those governed actions.
- Administrators can set existing source-document visibility from Django Admin. A research work's visibility remains the overall gate: setting only its source document to `admin` keeps the research metadata page available while blocking the PDF; setting the research work itself to `admin` hides the entire work from non-administrators.
- Production-grade LLM metadata extraction depends on provider configuration. Without a provider/API key, ingestion uses the deterministic fallback and all extracted suggestions remain in a read-only Review Queue. AI does not detect publication year or work type, and cannot modify any canonical research record.
- Vector chunks may exist without remote embeddings; semantic retrieval currently uses the local deterministic embedding baseline when available.
- AI teacher matching is deterministic and evidence-bound; it does not call an LLM and does not guarantee advisor availability.
- RAG answer generation, graph analytics/visualization, institutional SSO, and news processing remain out of scope.

## High-risk hard delete permissions

Normal `delete_*` permissions do not bypass protected governance records such as
lifecycle transitions, review decisions, and derived document evidence. When a
showcase or administrator environment needs direct deletion, assign the explicit
custom permissions from the Django user permission menu:

- `accounts | user | Can hard delete users referenced by governance audit records`
- `professors | professor | Can hard delete professors referenced by governed research records`
- `research | research work | Can hard delete research works and protected governance records`
- `documents | source document | Can hard delete source documents and protected derived records`

These permissions are intentionally separate from ordinary delete permissions.
They are meant for controlled cleanup only; routine production removal should
prefer archive/deactivate workflows.

## Three-tier production baseline

- `fin-web`: public Nginx/TLS ingress and collected static assets
- `fin-app`: Django/Gunicorn, Celery, Redis, application logs, private uploads, and environment secrets
- `fin-db`: PostgreSQL/pgvector and backups

Only the web tier should receive public HTTP(S). Do not expose PostgreSQL, Redis, Gunicorn development ports, or SSH to the WAN. Concrete host addresses and account identifiers are deliberately absent from the repository. Production DNS, TLS, firewall, backup target, storage placement, and restore drill remain deployment decisions.

The Nginx example in `deploy/nginx/research-platform.conf` uses placeholders and deliberately has no direct protected-media alias.

## Phase boundary

Schemas and interfaces exist for `DocumentChunk`, vector documents/chunks, embeddings, retrieval/citations, prompt and AI request audit, AI extraction candidates, governed review decisions, and typed graph nodes/edges. Document ingestion, review transitions, semantic retrieval, graph neighbors, and evidence-based teacher matching now provide prototype-level baseline behavior.

The following remain out of scope for this delivery:

- Production LLM-only extraction without deterministic fallback
- RAG response generation
- Advanced graph analytics/visualization
- News ingestion and research-idea generation

## Secrets and licensing

Never commit `.env`, database passwords, router/Wi-Fi credentials, server credentials, or AI keys. Production secrets belong in the application host's secret environment and must be rotated according to the deployment policy.

No open-source license has been selected for this new project. Do not redistribute it until the owner chooses and adds a license.
