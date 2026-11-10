"""Gateway-only settings (the shared Settings covers URLs, secrets, Redis, DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GatewayConfig:
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False  # set COOKIE_SECURE=true behind TLS
    trusted_proxies: int = 0  # number of reverse proxies in front (X-Forwarded-For hops)
    max_upload_bytes: int = 25 * 1024 * 1024
    admin_email: str | None = None
    admin_password: str | None = None
    login_limit_per_account: int = 5  # attempts per window per (ip, email)
    login_limit_per_ip: int = 30
    login_window_seconds: int = 300

    @classmethod
    def from_env(cls) -> GatewayConfig:
        return cls(
            access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "15")),
            refresh_token_days=int(os.getenv("REFRESH_TOKEN_DAYS", "7")),
            cookie_secure=_bool("COOKIE_SECURE", False),
            trusted_proxies=int(os.getenv("TRUSTED_PROXIES", "0")),
            admin_email=os.getenv("ADMIN_EMAIL") or None,
            admin_password=os.getenv("ADMIN_PASSWORD") or None,
        )
