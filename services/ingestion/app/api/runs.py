"""Import run status and the downloadable error report."""

from __future__ import annotations

import csv
import io
import uuid

from flask import Blueprint, Response, jsonify, request
from rp_shared.auth import require_min_role, require_service_token
from rp_shared.errors import NotFound
from sqlalchemy import func, select

from ..models import ImportRun
from . import deps

bp = Blueprint("runs", __name__, url_prefix="/ingestion")


def serialize_run(r: ImportRun, *, with_errors: bool = True) -> dict:
    out = {
        "id": str(r.id),
        "upload_id": str(r.upload_id),
        "profile_id": str(r.profile_id) if r.profile_id else None,
        "profile_version": r.profile_version,
        "dry_run": r.dry_run,
        "status": r.status,
        "rows_total": r.rows_total,
        "rows_ok": r.rows_ok,
        "rows_failed": r.rows_failed,
        "tables": r.tables or {},
        "error_message": r.error_message,
        "errors_truncated": r.errors_truncated,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }
    if with_errors:
        out["errors"] = r.errors or []
    return out


def _get_run(run_id: uuid.UUID) -> ImportRun:
    run = deps.session().get(ImportRun, run_id)
    if run is None:
        raise NotFound("Import run not found")
    return run


@bp.get("/runs")
@require_service_token
@require_min_role("viewer")
def list_runs():
    s = deps.session()
    page, size = deps.page_params()
    q = select(ImportRun)
    if upload_id := request.args.get("upload_id"):
        try:
            q = q.where(ImportRun.upload_id == uuid.UUID(upload_id))
        except ValueError:
            raise NotFound("Unknown upload") from None
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(
        q.order_by(ImportRun.created_at.desc()).limit(size).offset((page - 1) * size)
    ).all()
    return jsonify(
        deps.page_response([serialize_run(r, with_errors=False) for r in rows], total, page, size)
    )


@bp.get("/runs/<uuid:run_id>")
@require_service_token
@require_min_role("viewer")
def get_run(run_id):
    return jsonify(serialize_run(_get_run(run_id)))


def _csv_safe(value) -> str:
    """Stop spreadsheet formula injection when the CSV is opened in Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.get("/runs/<uuid:run_id>/errors.csv")
@require_service_token
@require_min_role("viewer")
def errors_csv(run_id):
    run = _get_run(run_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["level", "row", "column", "message", "value"])
    for e in run.errors or []:
        writer.writerow(
            [
                e.get("level"),
                e.get("row"),
                _csv_safe(e.get("column")),
                _csv_safe(e.get("message")),
                _csv_safe(e.get("value")),
            ]
        )
    return Response(
        "﻿" + buf.getvalue(),  # BOM so Excel opens accents correctly
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="import-{run.id}-errors.csv"'},
    )
