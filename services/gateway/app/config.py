"""Gateway-only settings (the shared Settings covers URLs, secrets and Redis).
The gateway has no database: users, passwords and the audit log belong to the auth service."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    trusted_proxies: int = 0  # number of reverse proxies in front (X-Forwarded-For hops)
    max_upload_bytes: int = 25 * 1024 * 1024

    @classmethod
    def from_env(cls) -> GatewayConfig:
        return cls(trusted_proxies=int(os.getenv("TRUSTED_PROXIES", "0")))
