# Modules and boundaries

The platform is split into **three modules plus a shared auth service**. Each module owns its
data and talks to the others only over HTTP (REST through the gateway, events through Redis).

```
                        ┌───────────────┐
 browser ──► gateway ──►│ auth          │  users, login/refresh, JWT, audit log      [schema auth]
   (no DB)   │          └───────────────┘
             │          ┌───────────────┐
             ├────────► │ BUILDER       │  block  (block library, versions)          [schema blocks]
             │          │ documents     │  template (A4 layouts, versions, render)   [schema templates]
             │          └───────────────┘
             │          ┌───────────────┐
             ├────────► │ GENERATOR     │  ingestion (Excel -> tables, placeholders) [schemas ingestion, data]
             │          │ fill & export │  report (numbering, hydrate, PDF)          [schema reports]
             │          └───────────────┘
             │          ┌───────────────┐
             └────────► │ EDITOR        │  editor (revisions, locking, assets)       [schema editor]
                        └───────────────┘
```

| Module | Services | Needs at runtime |
|---|---|---|
| Auth | `auth` | nothing |
| Builder (document builder) | `block`, `template` | auth tokens; `ingestion` only for the keyword catalogue and sample data |
| Generator (document filler) | `ingestion`, `report` | Builder (published templates and pinned blocks) |
| Editor (rich text) | `editor` | Generator (report HTML), auth |

## Rules
1. A service never imports another service's code or reads its tables.
2. Authentication is the auth service's job. Browsers reach it through the gateway
   (`/api/auth`, `/api/users`); any module can verify its access tokens locally with the shared
   `JWT_SECRET` (`rp_shared.auth.decode_access_token`), or trust the gateway's identity headers.
3. Dependencies point one way: Editor -> Generator -> Builder -> Auth. No cycles.
4. Published template versions and block versions are immutable, so the Generator can cache
   them and keeps working if the Builder is briefly down.

## Known couplings (honest list)
- `rp_shared` holds the *document kernel* (`placeholders`, `template_render`, `schemas`): the one
  piece of domain logic that Builder and Generator must agree on, so previews and real reports
  never differ. Changing it is a change to both modules.
- The Generator calls `template`, `block` and `ingestion`; the Builder calls `ingestion` for the
  keyword catalogue (Generator -> Builder -> Generator at the data level only).
- One frontend serves all modules; each `src/features/*` folder maps to one module.
- Existing databases: the `auth` schema used to belong to the gateway's role. After upgrading run
  `ALTER SCHEMA auth OWNER TO rp_auth;` (and `ALTER TABLE ... OWNER TO rp_auth` per table), or
  recreate the dev database.
