"""JWT helpers, role checks and the internal service-token guard."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import current_app, g, request

from .errors import Forbidden, Unauthorized

ROLES = ("admin", "designer", "operator", "viewer")
ROLE_RANK = {"viewer": 0, "operator": 1, "designer": 2, "admin": 3}
USER_ID_HEADER = "X-User-Id"
USER_ROLE_HEADER = "X-User-Role"
SERVICE_TOKEN_HEADER = "X-Service-Token"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def make_access_token(
    secret: str, user_id: str, role: str, minutes: int = 15, extra: dict[str, Any] | None = None
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        **(extra or {}),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(secret: str, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token expired", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("Invalid token", code="TOKEN_INVALID") from exc


def require_service_token(fn: Callable) -> Callable:
    """Only the gateway / other services may call this endpoint."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = current_app.config["SETTINGS"].service_token
        supplied = request.headers.get(SERVICE_TOKEN_HEADER, "")
        if not hmac.compare_digest(supplied, expected):
            raise Unauthorized("Missing or invalid service token", code="SERVICE_TOKEN_INVALID")
        g.user_id = request.headers.get(USER_ID_HEADER)
        g.user_role = request.headers.get(USER_ROLE_HEADER)
        return fn(*args, **kwargs)

    return wrapper


def require_role(*allowed: str) -> Callable:
    """Use after require_service_token. 'admin' always passes."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = getattr(g, "user_role", None)
            if role is None:
                raise Unauthorized("Authentication required")
            if role != "admin" and role not in allowed:
                raise Forbidden(f"Role '{role}' cannot perform this action")
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_min_role(minimum: str) -> Callable:
    """Use after require_service_token. Hierarchy: viewer < operator < designer < admin."""
    if minimum not in ROLE_RANK:
        raise ValueError(f"unknown role {minimum!r}")

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = getattr(g, "user_role", None)
            if role is None:
                raise Unauthorized("Authentication required")
            if ROLE_RANK.get(role, -1) < ROLE_RANK[minimum]:
                raise Forbidden(f"Role '{role}' cannot perform this action")
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_min_role_or_internal(minimum: str) -> Callable:
    """Like `require_min_role`, but a call that carries a valid service token and *no* user
    (service-to-service, e.g. the template service reading a block) is allowed too.

    Browser traffic always reaches a service through the gateway, which attaches the user's role,
    so a missing role can only come from a trusted internal caller. Use after
    `require_service_token`, and only on read endpoints.
    """
    if minimum not in ROLE_RANK:
        raise ValueError(f"unknown role {minimum!r}")

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = getattr(g, "user_role", None)
            if role is not None and ROLE_RANK.get(role, -1) < ROLE_RANK[minimum]:
                raise Forbidden(f"Role '{role}' cannot perform this action")
            return fn(*args, **kwargs)

        return wrapper

    return decorator
