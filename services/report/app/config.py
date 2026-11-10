"""Report-only settings (the shared Settings covers URLs, secrets, Redis and the database)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ReportConfig:
    timezone: str = "Europe/Paris"  # the day a report number belongs to
    max_batch: int = 200
    max_rows_per_table: int = 500  # rows a loop block may read from its data source
    pdf_timeout_ms: int = 30_000
    sync_jobs: bool = False  # run batches inline (tests) instead of in a background thread
    # Hosts Chromium may fetch images from while printing (data: URIs are always allowed).
    pdf_allowed_hosts: tuple[str, ...] = field(default_factory=tuple)
    storage_backend: str = "local"
    local_storage_dir: str = "/tmp/rp-reports"
    s3_endpoint: str | None = None
    s3_bucket: str = "reports"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> ReportConfig:
        endpoint = os.getenv("S3_ENDPOINT") or None
        hosts = tuple(h.strip() for h in os.getenv("PDF_ALLOWED_HOSTS", "").split(",") if h.strip())
        return cls(
            timezone=os.getenv("REPORT_TIMEZONE", "Europe/Paris"),
            max_batch=int(os.getenv("MAX_BATCH", "200")),
            pdf_timeout_ms=int(os.getenv("PDF_TIMEOUT_MS", "30000")),
            sync_jobs=_bool("SYNC_JOBS", False),
            pdf_allowed_hosts=hosts,
            storage_backend="s3" if endpoint else "local",
            local_storage_dir=os.getenv("LOCAL_STORAGE_DIR", "/tmp/rp-reports"),
            s3_endpoint=endpoint,
            s3_bucket=os.getenv("S3_BUCKET", "reports"),
            s3_access_key=os.getenv("S3_ACCESS_KEY") or None,
            s3_secret_key=os.getenv("S3_SECRET_KEY") or None,
            s3_region=os.getenv("S3_REGION", "us-east-1"),
        )
