# ADR-002: Service boundaries and communication

Status: accepted - 2026-11-06

## Decision
Six Flask services: gateway, ingestion, block, template, report, editor.

- A service reads and writes only its own schema (`auth`, `ingestion`+`data`, `blocks`,
  `templates`, `reports`, `editor`). Enforced by one Postgres role per service
  (`infra/postgres/init.sh`).
- Browser -> gateway only. The gateway validates the JWT and forwards `X-User-Id` /
  `X-User-Role`; services accept requests only with a valid `X-Service-Token`.
- Synchronous calls: REST through `rp_shared.service_client.ServiceClient`
  (timeout, retries with backoff, correlation id).
- Asynchronous: domain events on one Redis Stream (`rp.events`) with a consumer group per
  service (`rp_shared.events`). Handlers must be idempotent; failures stay pending.

## Fallback
If six services slow development, merge them into one Flask app and keep each as a
blueprint with the same schema ownership. The boundaries stay, the deployment shrinks.
