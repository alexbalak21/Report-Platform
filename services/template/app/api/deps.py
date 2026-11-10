"""Small helpers shared by the route modules."""

from __future__ import annotations

import uuid
from typing import Any

from flask import current_app, g, request
from rp_shared.errors import ValidationFailed
from sqlalchemy.orm import Session


def session() -> Session:
    if "db" not in g:
        factory = current_app.extensions.get("db_sessionmaker")
        if factory is None:
            raise RuntimeError("TEMPLATE_DATABASE_URL is not configured")
        g.db = factory()
    return g.db


def user_uuid() -> uuid.UUID | None:
    try:
        return uuid.UUID(str(g.get("user_id")))
    except ValueError:
        return None


def json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationFailed("Expected a JSON object body")
    return body


def page_params() -> tuple[int, int]:
    def _int(name: str, default: int) -> int:
        try:
            return int(request.args.get(name, default))
        except ValueError:
            raise ValidationFailed(f"'{name}' must be an integer") from None

    page = max(1, _int("page", 1))
    size = min(200, max(1, _int("page_size", 50)))
    return page, size


def page_response(items: list, total: int, page: int, size: int) -> dict[str, Any]:
    return {"items": items, "total": total, "page": page, "page_size": size}
