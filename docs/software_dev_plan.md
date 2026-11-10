# Software Development Plan - Report Platform (web)

Version 1.0 - 2026-11-06
Stack: **Python 3.12 + Flask**, **PostgreSQL 16**, **Vue 3 + Vite + Tailwind CSS**, CKEditor 5, Redis, Docker.

This plan supersedes the stack choices in `00-architecture.md` (FastAPI -> Flask). The functional content of plans 01-05 still applies.

---

## 1. Vision and scope

Replace the Windows desktop Report Generator with a web platform:

1. Upload an Excel file; the server maps columns/rows to database tables using an **import profile**.
2. Design A4 report templates visually from drag-and-drop **blocks** (default and custom).
3. Build custom blocks on a dedicated page.
4. Generate reports by replacing `{{keywords}}` with data.
5. Edit the generated A4 HTML in a rich text editor, then finalize to PDF.

### In scope for v1
Everything above, single organization (single-tenant, with users and roles), HTML + PDF output, French/English UI-ready (i18n scaffolding).

### Out of scope for v1 (backlog)
Multi-tenancy, DOCX export, chart blocks, real-time collaboration, e-signature with legal value, SSO.

### Assumptions (change if wrong)
- Single organization first; schemas include `org_id` nullable to ease a later switch.
- PDF + HTML only; DOCX is a v2 item.
- Deployment: one VPS / small cloud host with Docker Compose.
- Users are internal lab staff (tens, not thousands). Performance targets are modest.

---

## 2. Architecture

### 2.1 Services

| Service | Port (dev) | Responsibility | DB schema |
|---|---|---|---|
| gateway | 8000 | JWT auth, routing, CORS, rate limit, serves SPA in prod (via nginx) | `auth` |
| ingestion | 8001 | Uploads, import profiles, import jobs, data tables, placeholder catalog | `ingestion`, `data` |
| block | 8002 | Default/custom blocks, versions, sanitizing, preview | `blocks` |
| template | 8003 | Templates, versions, rendering, validation | `templates` |
| report | 8004 | Placeholder engine, generation, numbering, PDF export | `reports` |
| editor | 8005 | Documents, revisions, assets, locking | `editor` |
| worker | - | RQ worker for imports, batch generation, PDF | - |
| frontend | 5173 | Vue SPA | - |
| postgres | 5432 | One instance, one schema + one DB role per service | - |
| redis | 6379 | Job queue (RQ) + pub/sub events | - |
| minio | 9000 | S3-compatible file storage | - |

**Rule:** a service only reads/writes its own schema. Its DB role has no privileges on other schemas.

### 2.2 Communication

- **Sync:** browser -> gateway -> service (REST/JSON). Service -> service via internal HTTP with a service token header `X-Service-Token`, using a small `service_client` helper (timeouts, retries with backoff, correlation id).
- **Async:** Redis Streams for events (consumer groups so each service processes once). Event envelope:
```json
{ "id": "uuid", "type": "template.published", "ts": "2026-11-06T20:00:00Z",
  "actor": "user_id", "correlation_id": "uuid", "data": { } }
```
- **Jobs:** RQ queues `imports`, `reports`, `pdf`.
- **Live progress to the browser:** Server-Sent Events from the gateway (`GET /api/events/stream`), fed by the event stream.

### 2.3 Diagram (logical)

```
Vue SPA --> Gateway --+--> ingestion --> Postgres(ingestion,data) + MinIO
                      +--> block     --> Postgres(blocks)
                      +--> template  --> Postgres(templates)
                      +--> report    --> Postgres(reports) + MinIO + Playwright
                      +--> editor    --> Postgres(editor)  + MinIO
          Redis (events + queues) connects all services and the worker
```

### 2.4 Key design decisions (with rationale)

| Decision | Choice | Why |
|---|---|---|
| Template storage | Structured JSON (TemplateDocument), not raw HTML | Editable by the builder, validatable, versionable, renderable server-side |
| Body layout | Flow layout (blocks stack vertically) + optional absolutely positioned overlay blocks | Dynamic tables grow; pure absolute positioning breaks pagination |
| PDF | Headless Chromium (Playwright) | Best fidelity for A4 HTML/CSS |
| Placeholder engine | Custom restricted engine | No code execution, predictable, ports your operations |
| Block pinning | Templates pin `block_id + version` | Editing a block never silently changes existing templates |
| Sanitizing | `nh3` (Rust Ammonia binding) allow-list, shared lib | User-authored HTML must be safe |
| Rich text | CKEditor 5 | Chosen by you; see licensing note |
| Shared code | `shared/` Python package installed in every service | Contracts, sanitizer, auth helpers, event bus, service client |

### 2.5 Licensing note
CKEditor 5 open-source use is GPL-2.0-or-later; closed-source distribution requires a commercial license. Internal use on your own server is generally fine, but verify. Keep the editor behind an interface (`EditorAdapter`) so it can be swapped for TipTap (MIT).

---

## 3. Repository layout (monorepo)

```
report-platform/
├── docker-compose.yml
├── docker-compose.dev.yml
├── Makefile
├── .env.example
├── .github/workflows/ci.yml
├── docs/
│   ├── software_dev_plan.md
│   ├── plans/ (00..05)
│   └── adr/                     # architecture decision records
├── shared/                      # pip-installable package "rp_shared"
│   ├── pyproject.toml
│   └── rp_shared/
│       ├── config.py            # env-based settings
│       ├── db.py                # SQLAlchemy base, session helpers
│       ├── auth.py              # JWT verify, @require_role
│       ├── events.py            # publish/consume Redis Streams
│       ├── service_client.py    # inter-service HTTP
│       ├── errors.py            # error types + Flask handlers
│       ├── sanitizer.py         # nh3 allow-list
│       ├── placeholders.py      # grammar, parser, validator
│       ├── schemas/             # JSON Schema + Pydantic models
│       │   ├── import_profile.py
│       │   ├── block.py
│       │   ├── template_document.py
│       │   └── report_document.py
│       └── logging.py           # JSON logs + correlation id
├── services/
│   ├── gateway/    ingestion/    block/    template/    report/    editor/
│   │   ├── app/
│   │   │   ├── __init__.py      # create_app()
│   │   │   ├── api/             # blueprints
│   │   │   ├── models/          # SQLAlchemy models
│   │   │   ├── services/        # business logic
│   │   │   ├── events/          # consumers
│   │   │   └── jobs/
│   │   ├── migrations/          # Flask-Migrate (Alembic), own version table
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
├── frontend/
│   ├── package.json  vite.config.ts  tailwind.config.ts
│   └── src/
│       ├── main.ts  App.vue  router/  stores/ (Pinia)
│       ├── api/                 # typed clients per service
│       ├── components/ui/       # buttons, modals, tables (Tailwind)
│       ├── features/
│       │   ├── auth/  upload/  profiles/  blocks/  templates/  reports/  editor/
│       ├── i18n/ (fr.json, en.json)
│       └── types/               # generated from JSON Schemas
└── scripts/                     # seed data, dev helpers
```

---

## 4. Technology details

### Backend
- Flask 3, Flask-SQLAlchemy 3 / SQLAlchemy 2, Flask-Migrate, Flask-CORS (gateway only).
- Validation: **Pydantic v2** models in `shared/schemas`; JSON Schema exported for the frontend type generation (`datamodel-code-generator` -> Python, `json-schema-to-typescript` -> TS).
- Auth: `PyJWT`, passwords with `argon2-cffi`. Access token 15 min + refresh token 7 days (httpOnly cookie).
- Excel: `openpyxl` (formulas: read cached values with `data_only=True`; warn if None).
- Jobs: `rq` + Redis. Events: Redis Streams.
- PDF: `playwright` (Chromium) in the report worker image.
- Storage: `boto3` against MinIO/S3.
- Server: `gunicorn` (gthread workers) behind nginx. Dev: `flask run`.
- Tests: `pytest`, `pytest-postgresql` / testcontainers, `responses` for service mocks, `factory-boy`.
- Quality: `ruff`, `black`, `mypy` (shared package strict), `pre-commit`.

### Frontend
- Vue 3 (Composition API, `<script setup lang="ts">`), Vite, TypeScript, Pinia, Vue Router, vue-i18n.
- Tailwind CSS 3 (+ `@tailwindcss/forms`, `@tailwindcss/typography`); component primitives via Headless UI Vue.
- Drag and drop: `vue-draggable-plus` (SortableJS) for palette -> page and reordering; `interactjs` for resize/free-move of overlay blocks.
- Rich text: `@ckeditor/ckeditor5-vue` + `ckeditor5` modules.
- Data grid: `@tanstack/vue-table` + virtual scrolling for large tables.
- HTTP: `axios` with interceptors (token refresh, correlation id, error toasts).
- Tests: Vitest + Vue Test Utils, Playwright for e2e.
- Icons: `lucide-vue-next`.

### Infrastructure
- Docker Compose (dev + prod), nginx reverse proxy, Let's Encrypt (Caddy is an easier alternative).
- GitHub Actions CI. Backups: nightly `pg_dump` + MinIO bucket sync.
- Observability: structured JSON logs, `/healthz` and `/readyz` per service, Prometheus metrics optional (v2).

---

## 5. Cross-cutting specifications

### 5.1 Placeholder grammar (single source: `rp_shared/placeholders.py`)

```
{{ key }}                           value
{{ key | op1 | op2:arg }}           operation chain
{{ #each rows }} ... {{ /each }}    loop over a list (inside: {{ col }} or {{ this.col }})
{{ #if key }} ... {{ else }} ... {{ /if }}
{{ page }} {{ pages }}              footer only
{{ today | date:%d/%m/%Y }}         built-ins: today, now, report_number
```
- `key`: `[a-z][a-z0-9_]*` with optional dots (`sample.lot`). Keys with spaces from Excel are **normalized to snake_case** at import time (`nom entreprise client` -> `nom_entreprise_client`); the original header is kept as label.
- Operations (ported from desktop): `date:fmt`, `upper`, `lower`, `multiply:n`, `round:n`, `suffix:str`, `prefix:str`, `default:str`, `concat`, `lookup:table.col`. Add `number:locale` (thousand separators).
- Values HTML-escaped by default; `| raw` only for trusted fields.
- Precedence: operations apply left to right. (Past bug: operator precedence in lookups - write explicit unit tests.)
- Parser outputs an AST; the validator returns `[{key, line, col, error}]`.
- Normalize CRLF -> LF before parsing (past bug).

### 5.2 Error format (all services)
```json
{ "error": { "code": "TEMPLATE_INVALID", "message": "Unknown keyword customer_nr",
             "details": [{"key":"customer_nr"}], "correlation_id": "..." } }
```
HTTP: 400 validation, 401, 403, 404, 409 conflict (version), 422 semantic, 500 internal.

### 5.3 Auth and roles
Roles: `admin` (everything, users), `designer` (blocks, templates, profiles), `operator` (upload, generate, edit reports), `viewer` (read reports). Gateway validates JWT and forwards `X-User-Id`, `X-User-Role` to services; services trust only requests carrying the valid service token from the gateway.

### 5.4 Versioning and optimistic concurrency
Every mutable resource has `version` int; `PUT` requires `If-Match: <version>` (or body `base_version`); mismatch -> 409 with the latest.

### 5.5 Pagination, filtering
`?page=1&page_size=50&sort=-created_at&q=text` -> `{ items, total, page, page_size }`.

### 5.6 Security checklist
- All user HTML sanitized on write (blocks, editor) AND on render to PDF.
- PDF renderer: Chromium with network disabled except the internal asset host; no JS execution in report pages.
- Upload limits: 20 MB, extension + magic bytes check (`.xlsx` is a zip with `[Content_Types].xml`), zip-bomb guard (limit uncompressed size and sheet/row counts).
- SQL via ORM/parameters only; dynamic table names validated against an allow-list.
- CSRF: refresh cookie `SameSite=Strict`; API uses bearer header.
- Rate limiting on login and upload (gateway, Flask-Limiter + Redis).
- Secrets only from env; `.env` never committed.
- Audit log table in `auth` (who generated/finalized/deleted what).

---

## 6. Data model per service

### 6.1 gateway / `auth`
```sql
users(id uuid pk, email citext unique, password_hash text, display_name text,
      role text check (role in ('admin','designer','operator','viewer')),
      is_active bool default true, created_at timestamptz, last_login timestamptz)
refresh_tokens(id uuid pk, user_id fk, token_hash text, expires_at timestamptz, revoked bool)
audit_log(id bigserial pk, user_id uuid, action text, resource_type text,
          resource_id text, meta jsonb, created_at timestamptz)
```

### 6.2 ingestion / `ingestion` + `data`
```sql
import_profiles(id uuid pk, name text unique, version int, definition jsonb,
                 created_by uuid, created_at, updated_at)
uploads(id uuid pk, filename text, storage_key text, size_bytes bigint,
        status text check (status in ('uploaded','previewed','importing','done','failed')),
        profile_id uuid null, uploaded_by uuid, created_at)
import_runs(id uuid pk, upload_id fk, profile_id fk, profile_version int,
            rows_total int, rows_ok int, rows_failed int,
            errors jsonb, started_at, finished_at, status text)
data_tables(id uuid pk, name text unique, label text, key_columns text[])  -- registry
data_columns(id uuid pk, table_id fk, name text, label text, type text, position int)
-- Business tables: created by the import engine in schema `data`,
-- naming rule: data.<table_name>, columns typed per profile,
-- plus system columns: _id uuid pk, _source_upload uuid, _row_hash text,
-- _created_at, _updated_at, _report_number text null
```
Dynamic DDL: the import engine issues `CREATE TABLE`/`ALTER TABLE ADD COLUMN` only for validated identifiers (`^[a-z][a-z0-9_]{0,62}$`). Never drop columns automatically.

Import profile definition (JSON, validated by Pydantic):
```json
{
  "match": {"sheet": "Rapport", "required_headers": ["date rapport", "lot"]},
  "header_row": 1,
  "first_data_row": 2,
  "stop_on_blank_rows": 3,
  "tables": [{
    "table": "samples",
    "key": ["numero_echantillon"],
    "columns": [
      {"source": "date rapport", "target": "report_date", "type": "date", "format": "%d/%m/%Y", "required": true},
      {"source": "imp", "target": "imp_pct", "type": "number",
       "operations": [{"type":"multiply","value":100},{"type":"round","decimals":0}]},
      {"source": "espece", "target": "species_name", "type": "string",
       "lookup": {"sheet": "especes", "key_column": "code", "value_column": "nom"}}
    ]
  }]
}
```

### 6.3 block / `blocks`
```sql
blocks(id uuid pk, kind text check (kind in ('default','custom')), name text,
       category text, description text, owner_id uuid null, is_archived bool,
       current_version int, created_at, updated_at)
block_versions(id uuid pk, block_id fk, version int, definition jsonb,
               html_sanitized text, css_sanitized text,
               created_by uuid, created_at, unique(block_id, version))
```
Block definition:
```json
{
  "html": "...", "css": "...",
  "props_schema": {"title": {"type":"string","default":"Results"}},
  "placeholders": ["lot","imp_pct"],
  "default_size_mm": {"w":180,"h":40},
  "resizable": true, "repeatable_region": "header|footer|body|any"
}
```
CSS is **scoped** automatically: every selector is prefixed with `.blk-<block_id>` at sanitize time (use `tinycss2`), forbidding `@import`, `url()` to external hosts, `position: fixed`.

### 6.4 template / `templates`
```sql
templates(id uuid pk, name text, description text, status text
          check (status in ('draft','published','archived')),
          current_version int, published_version int null,
          created_by uuid, created_at, updated_at)
template_versions(id uuid pk, template_id fk, version int, document jsonb,
                  created_by uuid, created_at, unique(template_id, version))
template_block_usage(template_id uuid, version int, block_id uuid, block_version int)
```
TemplateDocument:
```json
{
  "schema_version": 1,
  "page": {"size":"A4","orientation":"portrait","margins_mm":{"t":20,"r":15,"b":20,"l":15}},
  "regions": {
    "header": {"height_mm": 25, "items": [BlockInstance]},
    "body":   {"items": [BlockInstance]},
    "footer": {"height_mm": 15, "items": [BlockInstance]}
  },
  "overlays": [BlockInstance with x_mm,y_mm,w_mm,h_mm,z],
  "settings": {"page_numbering": true, "first_page_header": true}
}
BlockInstance = {"instance_id":"uuid","block_id":"uuid","block_version":2,
                 "locked":false,"props":{},"bindings":{"data_source":"samples"}}
```

### 6.5 report / `reports`
```sql
reports(id uuid pk, template_id uuid, template_version int,
        record_table text, record_id uuid, report_number text unique,
        status text check (status in ('generating','draft','final','failed')),
        html_key text, pdf_key text null, unresolved_keys jsonb,
        created_by uuid, created_at, finalized_at null)
report_counters(day date pk, last_n int not null)
generation_jobs(id uuid pk, kind text, status text, total int, done int,
                error text null, created_by uuid, created_at, finished_at)
generation_job_items(job_id fk, report_id uuid null, record_id uuid, status text, error text)
```

### 6.6 editor / `editor`
```sql
documents(id uuid pk, report_id uuid unique, current_revision int,
          locked_by uuid null, lock_expires_at timestamptz null, status text, updated_at)
revisions(id uuid pk, document_id fk, number int, html_key text,
          author_id uuid, note text, created_at, unique(document_id, number))
assets(id uuid pk, document_id fk null, owner_id uuid, kind text
       check (kind in ('image','signature')), storage_key text, mime text,
       width int, height int, created_at)
```

---

## 7. API specification (v1)

All paths prefixed `/api` at the gateway and routed by first segment. `[R]` = role required.

### 7.1 Auth (gateway)
| Method | Path | Notes |
|---|---|---|
| POST | /auth/login | `{email,password}` -> access token + refresh cookie |
| POST | /auth/refresh | rotates refresh token |
| POST | /auth/logout | revokes refresh |
| GET | /auth/me | current user |
| CRUD | /users | [admin] |

### 7.2 Ingestion
| Method | Path | Notes |
|---|---|---|
| POST | /ingestion/uploads | multipart, returns upload |
| GET | /ingestion/uploads, /uploads/{id} | list/status |
| GET | /ingestion/uploads/{id}/preview | sheets, headers, 50 rows, suggested profile(s) with score |
| POST | /ingestion/uploads/{id}/import | `{profile_id, dry_run?}` -> run id (job) |
| GET | /ingestion/runs/{id} | progress + errors |
| GET | /ingestion/runs/{id}/errors.csv | download |
| CRUD | /ingestion/profiles [designer] | |
| POST | /ingestion/profiles/{id}/validate | check against an upload |
| GET | /ingestion/tables | registry |
| GET | /ingestion/tables/{name}/rows | filter/sort/page |
| GET | /ingestion/tables/{name}/rows/{id} | one record (used by report svc) |
| PATCH | /ingestion/tables/{name}/rows/{id} | internal: write back `_report_number` |
| GET | /ingestion/placeholders | catalog |

### 7.3 Block
| Method | Path |
|---|---|
| GET | /blocks?kind=&category=&q= |
| GET | /blocks/{id}, /blocks/{id}/versions/{v} |
| POST | /blocks [designer] |
| PUT | /blocks/{id} (new version, `If-Match`) |
| POST | /blocks/{id}/duplicate |
| POST | /blocks/{id}/archive |
| POST | /blocks/preview (draft definition + sample data -> sanitized HTML) |
| GET | /blocks/{id}/export, POST /blocks/import |

### 7.4 Template
| Method | Path |
|---|---|
| CRUD | /templates [designer] |
| GET | /templates/{id}/versions, /versions/{v} |
| PUT | /templates/{id}/draft (`If-Match`) |
| POST | /templates/{id}/publish |
| POST | /templates/{id}/validate |
| POST | /templates/{id}/render `{record?, mode:"preview"}` -> HTML |
| GET | /templates/{id}/usage |
| POST | /templates/{id}/upgrade-blocks (bump pinned versions) |

### 7.5 Report
| Method | Path |
|---|---|
| POST | /reports `{template_id, table, record_id}` |
| POST | /reports/batch `{template_id, table, record_ids[]}` -> job |
| GET | /reports, /reports/{id} |
| GET | /reports/{id}/html, /pdf |
| POST | /reports/{id}/finalize |
| POST | /reports/{id}/regenerate (discards manual edits, asks confirmation in UI) |
| GET | /reports/jobs/{id} |

### 7.6 Editor
| Method | Path |
|---|---|
| POST | /editor/documents `{report_id}` |
| GET | /editor/documents/{id} |
| PUT | /editor/documents/{id} `{html, base_revision}` |
| GET | /editor/documents/{id}/revisions, POST .../{n}/restore |
| POST/DELETE | /editor/documents/{id}/lock (+ heartbeat every 30 s, 2 min expiry) |
| POST | /editor/assets (multipart) |

OpenAPI: each service exposes `/openapi.json` using `flask-smorest` or `apiflask`; choose **APIFlask** (Pydantic-friendly). The gateway merges specs for a single docs page `/api/docs`.

### 7.7 Event catalog
| Event | Producer | Consumers | Effect |
|---|---|---|---|
| data.imported | ingestion | report, frontend (SSE) | refresh caches / notify |
| profile.updated | ingestion | - | informational |
| block.updated | block | template | flag templates with outdated blocks |
| template.published | template | report | invalidate template cache |
| report.generated | report | editor, SSE | create editor document |
| report.revised | editor | report | mark report as edited, invalidate PDF |
| report.finalized | report | editor | lock document |
| job.progress | any worker | SSE | progress bar |

---

## 8. Core algorithms

### 8.1 Import engine (ingestion)
1. Load workbook (`openpyxl`, read-only mode, `data_only=True`).
2. Match profile: for each profile compute score = fraction of `required_headers` present (case/space-insensitive); suggest those >= 0.8.
3. For each table in profile: iterate rows from `first_data_row`; build record per column rules (type coercion -> operations -> lookup).
4. Collect per-row errors (`{row, column, message, value}`); never abort the whole run for one bad row unless `strict` is set.
5. Upsert in batches of 500 using `INSERT ... ON CONFLICT (key) DO UPDATE` when `key` defined, else insert with `_row_hash` uniqueness to avoid duplicates on re-upload.
6. Single transaction per table; on `strict` + any error -> rollback.
7. Update `data_tables/data_columns` registry; publish `data.imported`.

Type coercion rules: dates accept Excel serials, `datetime`, and strings with the profile format; numbers accept comma decimals (`"12,5"`); empty string -> NULL; booleans (`oui/non/yes/no/1/0`).

### 8.2 Template rendering (shared by builder preview and report service)
1. Resolve blocks (`block_id@version`) via block service (cached 5 min by id+version; immutable so safe to cache long).
2. For each region, concatenate block HTML wrapped in `<section class="blk blk-<id>" data-instance="..." data-locked="true|false">`.
3. Inject scoped CSS and a base A4 stylesheet (`@page { size: A4; margin: ... }`).
4. Output: full HTML document with placeholders intact (builder preview with no record) or hydrated (with record).

### 8.3 Placeholder hydration (report service)
1. Build context: record columns (+ labels), built-ins (`today`, `report_number`), related rows for `#each` (from block `bindings.data_source`).
2. Parse each text node/attribute with the shared parser (operate on the HTML DOM using `lxml`, not regex, so tags are never broken).
3. Evaluate operations; HTML-escape; collect `unresolved_keys`.
4. Missing key policy per template setting: `error` | `blank` | `marker` (default `marker`, shows `[[key?]]` in red in drafts, and blocks finalize until resolved).

### 8.4 Report numbering
```sql
INSERT INTO report_counters(day,last_n) VALUES (:d,1)
ON CONFLICT (day) DO UPDATE SET last_n = report_counters.last_n + 1
RETURNING last_n;
```
Number = `yymmdd-N`. Atomic and race-free. After success, PATCH `_report_number` on the source record.

### 8.5 PDF export
Playwright Chromium, `page.set_content(html)`, `page.pdf(format='A4', print_background=True, display_header_footer=False)`; header/footer rendered as repeating `<thead>/<tfoot>` blocks or CSS `position: running()`-like fallback: use a wrapping `<table>` with `<thead>` (header) and `<tfoot>` (footer) so they repeat on each page. Page numbers: Chromium `headerTemplate/footerTemplate` with `<span class="pageNumber">`. Browser instance pooled per worker process.

### 8.6 Editor save flow
Client autosaves 2 s after last change with `base_revision`; server sanitizes, stores new revision HTML in object storage, updates `documents.current_revision`, emits `report.revised`. Conflict -> 409, UI offers "reload / overwrite".

---

## 9. Frontend plan

### 9.1 Routes
```
/login
/                       dashboard (recent uploads, reports)
/data/upload            upload wizard
/data/profiles          import profiles list/editor
/data/tables/:name      data grid
/blocks                 block library
/blocks/:id/edit        block builder
/templates              template list
/templates/:id/edit     template builder
/reports                report list + generate
/reports/:id/edit       report editor
/admin/users
```

### 9.2 State (Pinia stores)
`useAuthStore`, `useCatalogStore` (placeholders), `useBlocksStore`, `useTemplateEditorStore` (document, selection, history stack for undo/redo, dirty flag), `useReportsStore`, `useEditorStore`, `useJobsStore` (SSE-fed).

### 9.3 Template builder specifics
- Layout: three columns - palette (left, 280 px), canvas (center, scrollable, zoom 50-200%), inspector (right, 320 px). Matches your "palette left, page right" idea.
- Canvas: A4 as a div `210mm x 297mm` scaled with CSS `transform: scale()`; regions header/body/footer visually delimited; grid/snap toggle (5 mm); rulers.
- Drag from palette: `vue-draggable-plus` with `group: {name:'blocks', pull:'clone', put:false}` on palette, body list accepts. Header/footer accept only blocks whose `repeatable_region` allows.
- Overlay blocks: `interactjs` draggable/resizable with mm snapping, constrained inside page.
- Lock: toggles `locked`; locked blocks show a lock badge, ignore drag/resize/delete, inspector read-only unless unlocked.
- Undo/redo: command pattern, history of document patches (use `immer` + `produce-with-patches`), 100 steps.
- Autosave draft every 5 s when dirty; publish button runs `/validate` first and shows a checklist of issues.
- Preview: toggle "with data", pick a record from a combo; loads rendered HTML into a sandboxed `<iframe srcdoc sandbox>`.
- Keyboard: Ctrl+Z/Y, Del, arrows nudge, Ctrl+D duplicate, Ctrl+L lock.

### 9.4 Block builder specifics
Split view: form (name, category, size, props schema editor) + Monaco-like code editors (use `codemirror` 6 for HTML/CSS) + live preview iframe with sample record. Placeholder picker inserts `{{key}}` at the cursor. Version history sidebar with diff (`diff` lib).

### 9.5 Upload wizard (4 steps)
1. Drop file -> upload progress. 2. Preview sheets/headers/rows; suggested profile. 3. Map/adjust profile (column mapping table: Excel column, target, type, operations) - mirrors your planned mappings-editor (Title / Column / Placeholder / Operations). 4. Import -> live progress -> summary with downloadable error CSV.

### 9.6 Report editor specifics
CKEditor 5 Document editor in a centered A4 container (`210mm`, white, shadow). Custom plugins:
- `LockedBlocks`: elements with `data-locked="true"` -> `contenteditable=false` via the downcast/upcast converters and `isLimit`/object model.
- `SignatureInsert`: toolbar button inserts the user's stored signature image or opens a draw pad (`signature_pad`).
- `PlaceholderHighlight`: highlights leftover `{{...}}` / `[[...?]]`.
Page guides: dashed line every 297 mm minus margins (visual only). Finalize button: validates no unresolved keys, calls `/reports/{id}/finalize`, then shows PDF viewer.

### 9.7 UI/UX conventions (Tailwind)
Design tokens in `tailwind.config.ts` (colors `brand`, `surface`, `danger`; spacing; `font-sans: Inter`). Dark mode `class` strategy (optional v1.1). Reusable components: `BaseButton`, `BaseInput`, `BaseSelect`, `BaseModal`, `DataTable`, `Toast`, `ConfirmDialog`, `EmptyState`, `Spinner`, `Stepper`. Accessibility: focus rings, aria labels, keyboard operable drag alternatives (move up/down buttons in inspector).

---

## 10. Development phases and tasks

Estimates assume one developer, ~6 productive hours/day. Total: **~15-17 weeks**. Each phase ends with a demo and merged, tested code.

### Phase 0 - Foundation (1 week)
- [ ] Monorepo, Makefile, pre-commit (ruff, black, mypy, eslint, prettier)
- [ ] `docker-compose.yml`: postgres, redis, minio, services stubs, frontend
- [ ] `rp_shared`: config, logging w/ correlation id, errors, db helpers, service client, event bus
- [ ] DB init script creating schemas and roles per service
- [ ] Flask app factory template + `/healthz`, `/readyz`, Alembic setup per service
- [ ] CI pipeline (lint, tests, build images)
- [ ] ADR-001 stack, ADR-002 service boundaries, ADR-003 placeholder grammar
**Exit:** `make up` starts everything; CI green.

### Phase 1 - Gateway, auth, frontend shell (1.5 weeks)
- [ ] users, login, refresh rotation, roles, audit log
- [ ] Gateway proxy with JWT check, header forwarding, rate limit, error normalization
- [ ] Vue shell: layout, router guards, auth store, axios interceptors, i18n, Tailwind tokens, base UI kit
- [ ] SSE endpoint skeleton
**Exit:** log in, see dashboard, role-based menu.

### Phase 2 - Ingestion (3 weeks)
- [ ] Storage adapter (MinIO), upload endpoint with validations
- [ ] Excel reader (port from desktop `excel_reader.py`; keep lookup support and fix-proof `find_row`)
- [ ] Profile schema, CRUD, validator, header-matching suggestion
- [ ] Import engine (types, operations, lookups, upsert, errors), RQ job, progress events
- [ ] Dynamic table creation/migration + registry
- [ ] Data browsing API (filter/sort/page) + placeholder catalog
- [ ] Frontend: upload wizard, profile mapping editor, data grid, run history
- [ ] Tests using real sample files (`Rapport_data.xlsx`, `Data_coa.xlsx`, `data_with_lookups.xlsx`)
**Exit:** upload a sample workbook -> rows in Postgres -> catalog returns keywords.

### Phase 3 - Block service + builder (2 weeks)
- [ ] Sanitizer + CSS scoper in shared lib with tests (XSS corpus)
- [ ] CRUD, versions, duplicate, archive, import/export
- [ ] Seed default blocks: Header (logo + title + report no.), Footer (page x/y), Title, Rich text, Key/Value list, Results table, Signature box, Image, Spacer
- [ ] Preview endpoint
- [ ] Frontend: library page, block builder (form + CodeMirror + live preview + props schema editor)
**Exit:** duplicate "Results table", customize, save; appears in API palette query.

### Phase 4 - Template service + builder (3 weeks)
- [ ] TemplateDocument schema + validator (unknown block, unknown keyword, region rules)
- [ ] CRUD, draft/publish, versions, usage tracking, block upgrade
- [ ] Renderer (HTML assembly, scoped CSS, base A4 CSS)
- [ ] `block.updated` consumer
- [ ] Frontend: palette, canvas, DnD, overlays (move/resize), lock, inspector, undo/redo, autosave, validate/publish checklist, preview with data
**Exit:** build a template (header, footer, text, table), lock header, publish, preview with real row.

### Phase 5 - Report generator (2.5 weeks)
- [ ] Parser/AST/evaluator with full operations set (port desktop `processors.py` cases as tests)
- [ ] DOM-based hydration with `lxml`, loops, conditionals, escaping, unresolved keys
- [ ] Numbering with atomic counter + write-back to source record
- [ ] Report persistence, HTML storage, status machine
- [ ] Playwright PDF in worker image; header/footer repetition; page numbers
- [ ] Batch generation job with per-item status
- [ ] Frontend: report list, generate dialog (template + record picker grid), job progress, download, unresolved warnings
**Exit:** select row + template -> numbered PDF equal in content to desktop output.

### Phase 6 - Editor (2 weeks)
- [ ] Documents/revisions/assets API, locking with heartbeat, sanitizing on save
- [ ] CKEditor 5 integration (A4 container, toolbar, tables, images, source editing, autosave)
- [ ] Locked-block plugin, signature plugin, placeholder highlight
- [ ] Revision history UI + restore, finalize flow
- [ ] `report.generated` / `report.revised` / `report.finalized` event wiring
**Exit:** open generated report, add text + signature, revision 2 saved, finalize, PDF reflects edits.

### Phase 7 - Hardening and release (2 weeks)
- [ ] Security review against section 5.6, dependency audit (`pip-audit`, `npm audit`)
- [ ] e2e tests (Playwright) of the 3 golden paths (see section 11)
- [ ] Performance: import 50k rows < 60 s; report generation < 3 s (HTML) / < 8 s (PDF)
- [ ] Backups, restore drill, prod compose, TLS, env docs
- [ ] User guide (port `Creating-Templates-Mappings-Data.md` to new concepts), in-app help
- [ ] Data migration script: convert existing mapping JSON profiles (COA, fish quality) into import profiles + templates
**Exit:** v1.0 tagged and deployed.

---

## 11. Testing strategy

| Level | Tooling | Coverage target |
|---|---|---|
| Unit (parser, ops, sanitizer, coercion, numbering) | pytest | 90% on `rp_shared`, 80% services |
| API/integration | pytest + testcontainers Postgres/Redis | every endpoint: success + auth + validation + conflict |
| Contract | JSON Schema fixtures shared; consumer tests in each service | each event + inter-service endpoint |
| Frontend unit/component | Vitest + VTU | stores, builders' core logic |
| e2e | Playwright | golden paths below |

**Golden paths:** (1) login -> upload sample -> import -> browse rows; (2) duplicate block -> build template -> publish -> generate PDF; (3) open report -> edit -> sign -> finalize -> download.

**Regression tests to carry over from desktop bugs:** plain column mapping included in file-name rule; lookup `find_row` returns correct row (not first); placeholder split across runs not applicable in HTML but test inline-tag-wrapped placeholders (`<b>{{x}}</b>`); CRLF input; chained operations order; mapping completeness (every used keyword exists).

Security tests: XSS corpus on sanitizer, malformed xlsx/zip bomb, path traversal in filenames, role enforcement matrix.

---

## 12. DevOps

- **Branching:** trunk-based, short feature branches, PRs with CI required, conventional commits.
- **CI jobs:** lint -> unit -> integration (compose services) -> build images -> e2e (nightly + on main).
- **Environments:** dev (compose), staging (optional), prod.
- **Config:** 12-factor env vars; `.env.example` documents every variable.
- **Migrations:** each service runs `flask db upgrade` on start (guarded by advisory lock); backwards-compatible migrations only (expand/contract).
- **Backups:** nightly `pg_dump` (7 daily, 4 weekly), MinIO versioning; restore tested in Phase 7.
- **Releases:** semver tags; images tagged by version; changelog in `CHANGELOG.md`.

---

## 13. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Too many microservices for one developer | slow progress | Monorepo + shared lib + identical service skeleton; start with one Postgres; allow merging services later (boundaries kept as Flask blueprints) |
| CKEditor pagination/locking limits | UX gaps | Accept visual guides, rely on PDF; keep `EditorAdapter`; prototype locking in week 1 of Phase 6 |
| Dynamic tables from Excel get messy | data integrity | Registry tables, validated identifiers, no destructive migrations, type-widening only |
| Template/block drift | broken reports | Version pinning + upgrade flow + validation before publish |
| PDF layout differences | unhappy users | Single Chromium renderer, shared base CSS, visual regression tests |
| CKEditor license | legal | Decide before Phase 6; swap-ready adapter |
| Scope creep (charts, DOCX) | delays | Backlog; revisit after v1.0 |

---

## 14. Backlog (post v1)
DOCX export, chart blocks, multi-tenant orgs, SSO (OIDC), real-time co-editing, e-signature workflow, scheduled imports (watch folder/email), report approval workflow, template marketplace/import-export between instances, webhooks, Prometheus/Grafana.

---

## 15. Definition of done (every task)
Code merged via PR; tests added and green; lint/type checks clean; API documented in OpenAPI; migrations included and reversible; events documented in section 7.7; user-visible strings in i18n files; no secrets in code; short note in `docs/` if a decision was made (ADR).

---

## 16. Immediate next steps
1. Confirm the assumptions in section 1 (single-tenant, PDF only, CKEditor licensing).
2. Create the repo and implement **Phase 0**.
3. Convert plans 01-05 into `docs/plans/` and adjust FastAPI mentions to Flask/APIFlask.
