"""Ingestion-only settings (the shared Settings covers URLs, secrets, Redis, DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IngestionConfig:
    max_upload_bytes: int = 20 * 1024 * 1024
    max_uncompressed_bytes: int = 200 * 1024 * 1024  # zip-bomb guard
    max_sheets: int = 50
    max_rows_per_sheet: int = 200_000
    preview_rows: int = 50
    batch_size: int = 500
    max_stored_errors: int = 1000
    check_formulas: bool = True  # warn about formulas that have no cached value
    sync_jobs: bool = False  # run imports inline (tests) instead of in a background thread
    # Object storage: "local" (dev/tests) or "s3" (MinIO/S3)
    storage_backend: str = "local"
    local_storage_dir: str = "/tmp/rp-uploads"
    s3_endpoint: str | None = None
    s3_bucket: str = "ingestion"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> IngestionConfig:
        endpoint = os.getenv("S3_ENDPOINT") or None
        return cls(
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            max_rows_per_sheet=int(os.getenv("MAX_ROWS_PER_SHEET", "200000")),
            check_formulas=_bool("CHECK_FORMULAS", True),
            storage_backend="s3" if endpoint else "local",
            local_storage_dir=os.getenv("LOCAL_STORAGE_DIR", "/tmp/rp-uploads"),
            s3_endpoint=endpoint,
            s3_bucket=os.getenv("S3_BUCKET", "ingestion"),
            s3_access_key=os.getenv("S3_ACCESS_KEY") or None,
            s3_secret_key=os.getenv("S3_SECRET_KEY") or None,
            s3_region=os.getenv("S3_REGION", "us-east-1"),
        )
