"""Auth-service settings (the shared Settings covers URLs, secrets, Redis and the database)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthConfig:
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False  # set COOKIE_SECURE=true behind TLS
    # The refresh cookie is scoped to the URL browsers use (the gateway serves us under /api).
    cookie_path: str = "/api/auth"
    # Hops of reverse proxy in front of this service (the gateway counts as 1): the client IP
    # for rate limiting is then read from X-Forwarded-For.
    trusted_proxies: int = 1
    admin_email: str | None = None
    admin_password: str | None = None
    login_limit_per_account: int = 5  # attempts per window per (ip, email)
    login_limit_per_ip: int = 30
    login_window_seconds: int = 300

    @classmethod
    def from_env(cls) -> AuthConfig:
        return cls(
            access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "15")),
            refresh_token_days=int(os.getenv("REFRESH_TOKEN_DAYS", "7")),
            cookie_secure=_bool("COOKIE_SECURE", False),
            cookie_path=os.getenv("COOKIE_PATH", "/api/auth"),
            trusted_proxies=int(os.getenv("TRUSTED_PROXIES", "1")),
            admin_email=os.getenv("ADMIN_EMAIL") or None,
            admin_password=os.getenv("ADMIN_PASSWORD") or None,
        )
