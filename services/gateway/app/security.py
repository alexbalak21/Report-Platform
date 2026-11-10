"""Bearer-token authentication for gateway routes + request body validation."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from flask import current_app, g, request
from pydantic import BaseModel, ValidationError
from rp_shared.auth import decode_access_token
from rp_shared.errors import Forbidden, Unauthorized, ValidationFailed

from .policy import RANK

M = TypeVar("M", bound=BaseModel)


def authenticate_request() -> None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Missing bearer token", code="TOKEN_MISSING")
    claims = decode_access_token(current_app.config["SETTINGS"].jwt_secret, token.strip())
    g.user_id = claims["sub"]
    g.user_role = claims["role"]


def require_auth(*roles: str) -> Callable:
    """Authenticate; if roles are given the caller needs at least the lowest of them
    (hierarchy: viewer < operator < designer < admin)."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            authenticate_request()
            if roles and RANK.get(g.user_role, -1) < min(RANK[r] for r in roles):
                raise Forbidden(f"Role '{g.user_role}' cannot perform this action")
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def parse_body(model: type[M]) -> M:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("A JSON object body is required")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ]
        raise ValidationFailed("Invalid request body", details=details) from exc
