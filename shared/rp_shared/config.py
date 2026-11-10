"""Environment-based settings (12-factor). One Settings object per process."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

SERVICE_NAMES = ("gateway", "auth", "ingestion", "block", "template", "report", "editor")


@dataclass(frozen=True)
class Settings:
    service_name: str
    database_url: str | None = None
    redis_url: str | None = None
    service_token: str = "dev-service-token"
    jwt_secret: str = "dev-jwt-secret"
    log_level: str = "INFO"
    service_urls: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, service_name: str) -> Settings:
        prefix = service_name.upper()
        return cls(
            service_name=service_name,
            database_url=os.getenv(f"{prefix}_DATABASE_URL") or None,
            redis_url=os.getenv("REDIS_URL") or None,
            service_token=os.getenv("SERVICE_TOKEN", "dev-service-token"),
            jwt_secret=os.getenv("JWT_SECRET", "dev-jwt-secret"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            service_urls={
                name: os.environ[f"{name.upper()}_URL"]
                for name in SERVICE_NAMES
                if os.getenv(f"{name.upper()}_URL")
            },
        )
