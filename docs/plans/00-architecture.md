# 00 - Architecture Overview

## Goal
Turn the desktop Report Generator into a web app: upload Excel -> store in DB -> design A4 templates from blocks -> generate reports by replacing `{{keywords}}` -> edit the result in a rich text editor.

## Services

| # | Service | Role | Owns (DB schema) |
|---|---|---|---|
| 0 | **Gateway / Auth** | Single entry point, auth, routing | `auth` (users, orgs) |
| 1 | **Ingestion** | Upload Excel, apply an *import profile*, load rows into tables | `ingestion` + `data` (business tables) |
| 2 | **Template** | Template builder backend: store/version templates | `templates` |
| 3 | **Block** | Block builder backend: default + custom block library | `blocks` |
| 4 | **Report Generator** | Hydrate template with data -> HTML/PDF/DOCX | `reports` |
| 5 | **Editor** | Edit generated report HTML (CKEditor 5), save revisions | `editor` (revisions) |
| - | **Web frontend** | One SPA with 4 areas: Upload, Template Builder, Block Builder, Report Editor | - |

Rule: **each service owns its own schema/tables and never reads another service's tables.** Cross-service access goes through APIs or events.

## Proposed stack (adjust freely)
- Backend: Python 3.12 + FastAPI (reuses your existing mapping/processing logic from the desktop app: `processors.py`, `excel_reader.py`, `word_processor.py`).
- DB: PostgreSQL (one instance, one schema per service; can split later).
- Messaging: Redis Streams or NATS for events; REST/JSON for synchronous calls.
- Frontend: React + TypeScript + Vite. Drag and drop: `dnd-kit`. Page canvas: plain HTML/CSS in mm units.
- Rich text: CKEditor 5.
- PDF rendering: Playwright (headless Chromium) - best fidelity for A4 HTML.
- Files: S3-compatible storage (MinIO locally) for uploaded xlsx and generated PDFs.
- Orchestration: Docker Compose (monorepo, one folder per service).

## Communication

**Sync (REST via gateway):** frontend -> gateway -> service. Service-to-service calls use internal URLs with a service token.

**Async events (examples):**
- `data.imported` (Ingestion) -> Report Generator may refresh "available rows" cache
- `template.published` (Template) -> Report Generator invalidates cached template
- `block.updated` (Block) -> Template service flags templates using that block
- `report.generated` (Report Generator) -> Editor can open it; frontend gets notified (SSE/WebSocket)
- `report.revised` (Editor) -> Report Generator updates final export

## Shared contracts (the glue)
Keep in a `contracts/` package (JSON Schema + generated TS/Python types):
1. **ImportProfile** - how an Excel maps to tables (Ingestion).
2. **Block** - definition of a block (Block service).
3. **TemplateDocument** - A4 page layout with block instances (Template service).
4. **PlaceholderCatalog** - list of available `{{keywords}}` and their data source (Ingestion exposes, Template/Block consume).
5. **ReportDocument** - hydrated HTML + metadata (Report Generator -> Editor).

## Placeholder syntax (decide once, everywhere)
`{{customer_number}}`, `{{sample.date}}`, and table loops `{{#rows}} ... {{/rows}}`. Reuse your existing operation chain (`date_format`, `multiply`, `round`, `suffix`, `lookup`, `concat`, `uppercase`...) as `{{value | round:0 | suffix:%}}` or keep it in the mapping JSON. Your desktop notes show placeholder syntax was a past gotcha: define the grammar and a validator in `contracts/` before anything else.

## Repo layout
```
report-platform/
  contracts/            # schemas + shared types
  services/
    gateway/  ingestion/  template/  block/  report/  editor/
  frontend/
  docker-compose.yml
  docs/plans/           # these md files
```

## Build order
1. contracts + docker-compose skeleton + gateway/auth
2. Ingestion (data must exist first)
3. Block service + Block builder UI
4. Template service + Template builder UI
5. Report Generator
6. Editor (CKEditor)
7. Hardening: permissions, versioning, tests, CI

## Open decisions
- Multi-tenant (orgs) from day one, or single-tenant first?
- DOCX export needed (existing app produced .docx) or PDF + HTML only?
- CKEditor 5 licensing: open-source use is GPL-2.0-or-later; if the app is closed-source/commercial you need a commercial license. Check before committing. TipTap is an MIT alternative.
