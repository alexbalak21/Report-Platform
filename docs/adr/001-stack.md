# ADR-001: Technology stack

Status: accepted - 2026-11-06

## Decision
Python 3.12 + Flask 3 (services), PostgreSQL 16, Redis 7 (events + queues), MinIO (files),
Vue 3 + Vite + TypeScript + Pinia + Tailwind 3 (frontend), CKEditor 5 (report editor, Phase 6),
Playwright/Chromium (PDF, Phase 5), Docker Compose (dev and prod).

## Why
- Flask/Python lets us reuse the desktop app's logic (`processors.py`, `excel_reader.py`).
- One Postgres instance with one schema + login role per service keeps ops simple while
  enforcing boundaries (a role cannot read another service's schema).
- Chromium gives the best A4 HTML -> PDF fidelity.

## Consequences
- Every service has a top-level package named `app`, so tests run one service per process
  (`make test` loops over services).
- CKEditor 5 is GPL-2.0-or-later: revisit before distributing outside your own server.
  Keep it behind an `EditorAdapter` so TipTap (MIT) can replace it.
