"""Batch generation. A small thread pool inside the process runs the job; its state lives in the
database, so the API only polls (same approach as the ingestion service's imports)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from rp_shared import events
from rp_shared.errors import AppError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .clients import Actor
from .generate import Deps, create_report, load_template
from .models import GenerationJob, GenerationJobItem, utcnow

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, sync: bool = False, workers: int = 2) -> None:
        self.pool = None if sync else ThreadPoolExecutor(workers, thread_name_prefix="batch")

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


def _progress(redis, job: GenerationJob, actor: Actor) -> None:
    if redis is None:
        return
    try:
        events.publish(
            redis,
            "job.progress",
            {
                "job_id": str(job.id),
                "kind": "report.batch",
                "status": job.status,
                "total": job.total,
                "done": job.done,
                "failed": job.failed,
            },
            actor.user_id,
        )
    except Exception:
        log.warning("could not publish job.progress", exc_info=True)


def run_batch(
    factory: sessionmaker, deps: Deps, redis, job_id: uuid.UUID, version: int | None, actor: Actor
) -> None:
    """Generate one report per queued item. A failing record never stops the others."""
    with factory() as s:
        job = s.get(GenerationJob, job_id)
        job.status = "running"
        s.commit()
        _progress(redis, job, actor)
        try:
            tpl = load_template(deps, job.template_id, version, actor)
        except Exception as exc:  # no template, no job
            job.status, job.error, job.finished_at = "failed", _message(exc), utcnow()
            s.commit()
            _progress(redis, job, actor)
            return
        job.template_version = tpl.version
        s.commit()

        items = s.scalars(select(GenerationJobItem).where(GenerationJobItem.job_id == job_id)).all()
        for item in items:
            try:
                report = create_report(deps, s, tpl, job.record_table, item.record_id, actor)
                item.status, item.report_id = "ok", report.id
                job.done += 1
            except AppError as exc:
                item.status = "failed"
                item.error = {"code": exc.code, "message": str(exc), "details": exc.details}
                job.failed += 1
            except Exception as exc:
                log.exception("batch item failed")
                item.status = "failed"
                item.error = {"code": "INTERNAL_ERROR", "message": _message(exc)}
                job.failed += 1
            s.commit()
            _progress(redis, job, actor)
        job.status = "failed" if job.failed == job.total else "done"
        job.finished_at = utcnow()
        s.commit()
        _progress(redis, job, actor)


def _message(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def recover_interrupted(factory: sessionmaker) -> int:
    """After a restart no batch can still be running: close the leftovers as failed."""
    with factory() as s:
        jobs = s.scalars(
            select(GenerationJob).where(GenerationJob.status.in_(("queued", "running")))
        ).all()
        for job in jobs:
            job.status, job.finished_at = "failed", utcnow()
            job.error = "Interrupted by a service restart; start the batch again"
        s.commit()
        return len(jobs)
