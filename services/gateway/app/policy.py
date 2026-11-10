"""Which role may call which proxied route.

Roles form a hierarchy (viewer < operator < designer < admin): a designer can also do
everything an operator can, so builders can test their own templates end to end.
The gateway routes by first path segment and forwards the path unchanged (minus `/api`).
"""

from __future__ import annotations

import re

RANK = {"viewer": 0, "operator": 1, "designer": 2, "admin": 3}

# first URL segment -> service
SEGMENT_SERVICE = {
    "ingestion": "ingestion",
    "blocks": "block",
    "templates": "template",
    "reports": "report",
    "editor": "editor",
}

# Handled by the auth service, which checks the bearer token and roles itself (login, refresh and
# logout are public by nature). The gateway forwards these without adding an identity.
AUTH_SEGMENTS = {"auth", "users", "audit"}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# POSTs that compute something without changing stored state
_READ_ONLY_POSTS = (
    re.compile(r"^blocks/preview$"),
    re.compile(r"^templates/[^/]+/(render|validate)$"),
)
# Internal write-back used by the report service (service token only, never from a browser)
_INTERNAL = (re.compile(r"^ingestion/tables/[^/]+/rows/[^/]+$"),)


def min_role(rel_path: str, method: str) -> str | None:
    """Minimum role for this request, or None when the route must not be exposed."""
    rel = rel_path.strip("/")
    segment = rel.split("/", 1)[0]
    method = method.upper()
    if segment not in SEGMENT_SERVICE:
        return None
    if method == "PATCH" and any(p.match(rel) for p in _INTERNAL):
        return None
    if method in SAFE_METHODS:
        return "viewer"
    if any(p.match(rel) for p in _READ_ONLY_POSTS):
        return "operator"
    if segment in {"blocks", "templates"}:
        return "designer"
    if segment == "ingestion" and rel.startswith("ingestion/profiles"):
        return "designer"
    return "operator"


def auth_route_exposed(rel_path: str, method: str) -> bool:
    """False for the one auth-service route that is for other services only (audit writes)."""
    return not (rel_path.strip("/").startswith("audit") and method.upper() != "GET")


def allowed(role: str, needed: str) -> bool:
    return RANK.get(role, -1) >= RANK[needed]
