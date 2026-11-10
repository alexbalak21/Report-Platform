"""Background execution of imports.

A small thread pool inside the service process keeps Phase 2 simple (run state lives in the
database, so the API only polls). The interface is one method, so it can be swapped for an RQ
worker queue later without touching the API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from .models import ImportRun, Upload, utcnow

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, sync: bool = False, workers: int = 2) -> None:
        self.pool = None if sync else ThreadPoolExecutor(workers, thread_name_prefix="import")

    def submit(self, fn: Callable, *args) -> None:
        if self.pool is None:
            fn(*args)
        else:
            self.pool.submit(self._safe, fn, *args)

    @staticmethod
    def _safe(fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("background job crashed")


def recover_interrupted(factory: sessionmaker) -> int:
    """After a restart no import can still be running: mark leftovers as failed."""
    with factory() as s:
        ids = s.scalars(
            select(ImportRun.id).where(ImportRun.status.in_(("queued", "running")))
        ).all()
        if not ids:
            return 0
        runs = s.scalars(select(ImportRun).where(ImportRun.id.in_(ids))).all()
        for r in runs:
            r.status, r.finished_at = "failed", utcnow()
            r.error_message = "Interrupted by a service restart; upload the file again"
        s.execute(
            update(Upload)
            .where(Upload.id.in_([r.upload_id for r in runs]), Upload.status == "importing")
            .values(status="failed")
        )
        s.commit()
        return len(ids)
