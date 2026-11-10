# ADR-004: Authentication, roles and gateway routing

Status: accepted - 2026-11-07

## Decision
- **Tokens.** Login returns a 15-minute JWT access token (kept in memory by the SPA) and sets a
  7-day opaque refresh token in an `HttpOnly; SameSite=Strict; Path=/api/auth` cookie. Refresh
  tokens are stored hashed and **rotated on every use**; all rotations of one login share a
  `family_id`. Presenting an already-rotated token revokes the whole family (theft detection).
- **No account enumeration.** Unknown email, wrong password and disabled account all return the
  same 401 and do comparable work (a dummy Argon2 verify runs for unknown emails).
- **Rate limiting.** Login is limited per (ip, email) and per ip (Redis fixed window, in-memory
  fallback). A successful login resets the per-account counter.
- **Roles are a hierarchy**: viewer < operator < designer < admin. A designer can do everything an
  operator can, so they can test their own templates end to end. `viewer` is read-only.
- **Routing.** The gateway routes by first path segment and forwards the path unchanged except for the
  `/api` prefix: `/api/blocks/1` reaches the block service as `/blocks/1`:
  `ingestion`, `editor` -> same-named services; `blocks`, `templates`, `reports` -> block,
  template, report. Own routes: `/api/auth`, `/api/users`, `/api/audit`, `/api/events`, `/api/health`.
- **Policy** lives in `services/gateway/app/policy.py` (one table, unit-tested). Writes need
  operator (reports, editor, uploads), designer (blocks, templates, import profiles).
  `PATCH /ingestion/tables/*/rows/*` is internal (report numbering write-back) and is never proxied.
- **Identity to services.** The gateway forwards `X-User-Id` / `X-User-Role` plus the service
  token; headers are rebuilt from an allow-list so clients cannot spoof them.
- **Audit log.** Auth events, user changes, and every successful proxied POST/DELETE. PUT/PATCH are
  excluded on purpose (autosave would flood the table).
- **Last-admin guard.** The only active admin cannot be demoted or deactivated.
- **Live events.** `GET /api/events/stream` (SSE) relays UI-relevant events from the `rp.events`
  stream. EventSource cannot send an Authorization header, so the SPA uses fetch streaming.

## Consequences
- Access tokens stay valid until they expire (<= 15 min) after a role change or deactivation;
  refresh is refused immediately.
- Every open SSE stream occupies a gunicorn thread, so the gateway runs 32 threads
  (`GUNICORN_THREADS`). Revisit (gevent/async) if concurrent users grow past a few dozen.
- Set `JWT_SECRET` to 32+ random bytes and `COOKIE_SECURE=true` in production; the gateway logs a
  warning for short secrets.
