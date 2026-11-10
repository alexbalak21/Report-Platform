"""Bearer-token authentication for the gateway's own routes (tokens come from the auth service)."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from flask import current_app, g, request
from rp_shared.auth import ROLE_RANK, decode_access_token
from rp_shared.errors import Forbidden, Unauthorized


def authenticate_request() -> None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Missing bearer token", code="TOKEN_MISSING")
    claims = decode_access_token(current_app.config["SETTINGS"].jwt_secret, token.strip())
    g.user_id = claims["sub"]
    g.user_role = claims["role"]


def require_auth(*roles: str) -> Callable:
    """Authenticate; if roles are given the caller needs at least the lowest of them."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            authenticate_request()
            if roles and ROLE_RANK.get(g.user_role, -1) < min(ROLE_RANK[r] for r in roles):
                raise Forbidden(f"Role '{g.user_role}' cannot perform this action")
            return fn(*args, **kwargs)

        return wrapper

    return decorator
