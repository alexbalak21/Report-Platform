# Report Platform

Web version of the Report Generator: upload Excel -> database -> design A4 templates from
blocks -> generate reports by replacing `{{keywords}}` -> edit in a rich text editor -> PDF.

Stack: Flask, PostgreSQL, Redis, MinIO, Vue 3 + Tailwind. Full plan: `docs/software_dev_plan.md`.

## Status

| Phase | State |
|---|---|
| 0 Foundation | done: monorepo, shared lib, compose, CI |
| 1 Auth + gateway | done: standalone `auth` service, DB-free gateway; login screen and shell in the frontend |
| 2 Ingestion | done (backend) + upload, profiles and data views |
| 3 Blocks | done (backend) + block library and builder |
| 4 Templates | backend done (CRUD, versions, validate, render); **builder UI not started** |
| 5 Reports | backend done (generate, batch, numbering, PDF, finalize); **UI not started** |
| 6 Editor | not started |
| 7 Hardening | not started |

Run each service's tests with `cd services/<name> && python -m pytest` (set `TEST_PG_URL` to
also run the Postgres variants).

Modules and rules: see [docs/modules.md](docs/modules.md).

## Quick start

```bash
make up            # copies .env.example to .env if missing, then builds and starts everything
# edit .env first and change every "change-me" value
```

- Frontend: http://localhost:5173 (shows the status of every service)
- Gateway: http://localhost:8000/api/health
- MinIO console: http://localhost:9001

Each service also listens on its own port for debugging (8001 ingestion, 8002 block,
8003 template, 8004 report, 8005 editor); each exposes `/healthz` and `/readyz`.

## Develop without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "shared[dev]"
make test lint
cd frontend && npm install && npm run dev
```

## Layout

```
shared/rp_shared/   config, logging, errors, db, events, service_client, auth,
                    sanitizer, placeholders (parser + renderer), app_factory
services/<name>/    one Flask app each (wsgi.py + app/), tests in tests/
services/Dockerfile one image recipe for all services (--build-arg SERVICE=...)
infra/postgres/     init.sh creates one schema + one login role per service
frontend/           Vue 3 + Vite + Tailwind (+ Vitest)
docs/               plan, ADRs, per-service plans
```

## Conventions
- A service only touches its own schema. Cross-service data goes through APIs or events.
- All errors use the envelope in `rp_shared.errors`.
- Every PR: tests green, `ruff check` clean, migrations reversible, strings in `i18n.ts`.
