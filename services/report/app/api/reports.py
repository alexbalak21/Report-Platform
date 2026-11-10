"""Reports: generate (one or a batch), read, regenerate, finalize, delete, PDF."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date
from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, current_app, g, jsonify, request
from rp_shared import events
from rp_shared.auth import require_min_role, require_service_token
from rp_shared.errors import AppError, Conflict, NotFound, Unprocessable, ValidationFailed
from sqlalchemy import func, or_, select

from .. import generate, jobs
from ..clients import check_table
from ..models import GenerationJob, GenerationJobItem, Report, utcnow
from . import deps

log = logging.getLogger(__name__)
bp = Blueprint("reports", __name__, url_prefix="/reports")


class PdfFailed(AppError):
    status = 502
    code = "PDF_FAILED"


def _deps() -> generate.Deps:
    return current_app.extensions["report_deps"]


def _get(report_id: uuid.UUID) -> Report:
    report = deps.session().get(Report, report_id)
    if report is None:
        raise NotFound("Report not found")
    return report


def _publish(type_: str, data: dict) -> None:
    redis = current_app.extensions.get("redis")
    if redis is None:
        return
    try:
        events.publish(redis, type_, data, g.get("user_id"))
    except Exception:  # best effort: the change is already committed
        log.warning("could not publish %s", type_, exc_info=True)


def _dto(r: Report) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "report_number": r.report_number,
        "report_date": r.report_date,
        "status": r.status,
        "template": {
            "id": str(r.template_id),
            "version": r.template_version,
            "name": r.template_name,
        },
        "record": {"table": r.record_table, "id": str(r.record_id)},
        "unresolved_keys": r.unresolved_keys or [],
        "record_synced": r.record_synced,
        "has_pdf": r.pdf_key is not None,
        "created_by": str(r.created_by) if r.created_by else None,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        "finalized_at": r.finalized_at,
    }


def _event_data(r: Report) -> dict[str, Any]:
    return {
        "report_id": str(r.id),
        "report_number": r.report_number,
        "template_id": str(r.template_id),
        "template_version": r.template_version,
        "record_table": r.record_table,
        "record_id": str(r.record_id),
    }


def _template_version(body: dict[str, Any]) -> int | None:
    raw = body.get("template_version")
    if raw is None:
        return None
    try:
        version = int(raw)
    except (TypeError, ValueError):
        raise ValidationFailed("'template_version' must be an integer") from None
    if version < 1:
        raise ValidationFailed("'template_version' must be at least 1")
    return version


def _day(body: dict[str, Any]) -> date | None:
    raw = body.get("report_date")
    if raw is None:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ValidationFailed("'report_date' must be an ISO date (YYYY-MM-DD)") from None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@bp.get("")
@require_service_token
@require_min_role("viewer")
def list_reports():
    s = deps.session()
    q = select(Report)
    args = request.args
    if status := args.get("status"):
        q = q.where(Report.status == status)
    if template_id := args.get("template_id"):
        try:
            q = q.where(Report.template_id == uuid.UUID(template_id))
        except ValueError:
            raise ValidationFailed("'template_id' must be a UUID") from None
    if table := args.get("table"):
        q = q.where(Report.record_table == table)
    if record_id := args.get("record_id"):
        try:
            q = q.where(Report.record_id == uuid.UUID(record_id))
        except ValueError:
            raise ValidationFailed("'record_id' must be a UUID") from None
    if term := (args.get("q") or "").strip():
        like = f"%{term.replace('%', '').replace('_', '')}%"
        q = q.where(or_(Report.report_number.ilike(like), Report.template_name.ilike(like)))
    page, size = deps.page_params()
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(
        q.order_by(Report.created_at.desc(), Report.id).limit(size).offset((page - 1) * size)
    ).all()
    return jsonify(deps.page_response([_dto(r) for r in rows], total, page, size))


@bp.get("/<uuid:report_id>")
@require_service_token
@require_min_role("viewer")
def get_report(report_id):
    return jsonify(_dto(_get(report_id)))


@bp.get("/<uuid:report_id>/html")
@require_service_token
@require_min_role("viewer")
def get_html(report_id):
    report = _get(report_id)
    html = _deps().storage.get(report.html_key)
    return Response(
        html,
        mimetype="text/html",
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src data: https:; "
            "style-src 'unsafe-inline'"
        },
    )


def _filename(report: Report) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", report.template_name).strip()
    return f"{name} {report.report_number}.pdf".strip()


@bp.get("/<uuid:report_id>/pdf")
@require_service_token
@require_min_role("viewer")
def get_pdf(report_id):
    """The stored PDF of a final report; a draft is printed on the fly (and not stored)."""
    report = _get(report_id)
    d = _deps()
    if report.pdf_key:
        data = d.storage.get(report.pdf_key)
    else:
        data = _print(report)
    filename = _filename(report)
    return Response(
        data,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Report-Status": report.status,
        },
    )


def _print(report: Report) -> bytes:
    d = current_app.extensions["report_deps"]
    html = d.storage.get(report.html_key).decode("utf-8")
    try:
        return current_app.extensions["pdf"].render(html)
    except Exception as exc:
        raise PdfFailed(f"The PDF could not be produced: {exc}") from exc


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
@bp.post("")
@require_service_token
@require_min_role("operator")
def create():
    body = deps.json_body()
    template_id = deps.uuid_field(body, "template_id")
    record_id = deps.uuid_field(body, "record_id")
    table = check_table(body.get("table"))
    actor = deps.actor()
    d = _deps()
    tpl = generate.load_template(d, template_id, _template_version(body), actor)
    report = generate.create_report(d, deps.session(), tpl, table, record_id, actor, _day(body))
    _publish("report.generated", _event_data(report))
    return jsonify({**_dto(report), "unresolved_count": len(report.unresolved_keys)}), 201


@bp.post("/batch")
@require_service_token
@require_min_role("operator")
def create_batch():
    body = deps.json_body()
    template_id = deps.uuid_field(body, "template_id")
    table = check_table(body.get("table"))
    raw = body.get("record_ids")
    d = _deps()
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("'record_ids' must be a non-empty list")
    if len(raw) > d.config.max_batch:
        raise ValidationFailed(f"A batch holds at most {d.config.max_batch} records")
    try:
        record_ids = list(dict.fromkeys(uuid.UUID(str(r)) for r in raw))
    except ValueError:
        raise ValidationFailed("'record_ids' must contain UUIDs") from None
    version = _template_version(body)
    actor = deps.actor()
    s = deps.session()
    job = GenerationJob(
        id=uuid.uuid4(),
        template_id=template_id,
        template_version=version,
        record_table=table,
        total=len(record_ids),
        created_by=deps.user_uuid(),
    )
    s.add(job)
    s.add_all(GenerationJobItem(job_id=job.id, record_id=r) for r in record_ids)
    s.commit()
    factory = current_app.extensions["db_sessionmaker"]
    current_app.extensions["jobs"].submit(
        jobs.run_batch, factory, d, current_app.extensions.get("redis"), job.id, version, actor
    )
    s.refresh(job)
    return jsonify(_job_dto(job, include_items=False)), 202


def _job_dto(job: GenerationJob, include_items: bool = True) -> dict[str, Any]:
    out = {
        "id": str(job.id),
        "status": job.status,
        "template_id": str(job.template_id),
        "template_version": job.template_version,
        "table": job.record_table,
        "total": job.total,
        "done": job.done,
        "failed": job.failed,
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }
    if include_items:
        items = deps.session().scalars(
            select(GenerationJobItem).where(GenerationJobItem.job_id == job.id)
        )
        out["items"] = [
            {
                "record_id": str(i.record_id),
                "report_id": str(i.report_id) if i.report_id else None,
                "status": i.status,
                "error": i.error,
            }
            for i in items
        ]
    return out


@bp.get("/jobs/<uuid:job_id>")
@require_service_token
@require_min_role("viewer")
def get_job(job_id):
    job = deps.session().get(GenerationJob, job_id)
    if job is None:
        raise NotFound("Job not found")
    return jsonify(_job_dto(job))


# ---------------------------------------------------------------------------
# Change
# ---------------------------------------------------------------------------
def _draft(report_id: uuid.UUID) -> Report:
    report = _get(report_id)
    if report.status != "draft":
        raise Conflict("This report is final and can no longer be changed", code="REPORT_FINAL")
    return report


@bp.post("/<uuid:report_id>/regenerate")
@require_service_token
@require_min_role("operator")
def regenerate(report_id):
    """Render the report again (same number). Manual edits are lost: the UI asks first.
    Body (optional): {"use_latest": true} to move to the newest published template version."""
    report = _draft(report_id)
    body = deps.json_body() if request.data else {}
    actor = deps.actor()
    d = _deps()
    version = None if body.get("use_latest") else report.template_version
    tpl = generate.load_template(d, report.template_id, version, actor)
    generate.regenerate_report(d, deps.session(), report, tpl, actor)
    _publish("report.generated", _event_data(report))
    return jsonify(_dto(report))


@bp.post("/<uuid:report_id>/finalize")
@require_service_token
@require_min_role("operator")
def finalize(report_id):
    """Lock the report and keep its PDF. Refused while a keyword is still unresolved."""
    report = _get(report_id)
    if report.status == "final":
        return jsonify({**_dto(report), "unchanged": True})
    d = _deps()
    html = d.storage.get(report.html_key).decode("utf-8")
    left = generate.leftover_keys(html)
    if left:
        raise Unprocessable(
            f"{len(left)} keyword(s) still have no value: fix them in the editor first",
            code="REPORT_UNRESOLVED_KEYS",
            details=[{"key": k} for k in left],
        )
    try:
        pdf = current_app.extensions["pdf"].render(html)
    except Exception as exc:
        raise PdfFailed(f"The PDF could not be produced: {exc}") from exc
    key = generate.pdf_key(report.id)
    d.storage.put(key, pdf)
    report.pdf_key = key
    report.status = "final"
    report.unresolved_keys = []
    report.finalized_at = utcnow()
    report.finalized_by = deps.user_uuid()
    deps.session().commit()
    if not report.record_synced:
        generate.sync_record(d, deps.session(), report)
    _publish("report.finalized", _event_data(report))
    return jsonify({**_dto(report), "unchanged": False})


@bp.delete("/<uuid:report_id>")
@require_service_token
@require_min_role("operator")
def delete(report_id):
    """Remove a draft. Its number is not reused (a gap is honest); the record is free again and
    its stored number follows the newest remaining report, if any."""
    report = _draft(report_id)
    s = deps.session()
    d = _deps()
    table, record_id, html_key = report.record_table, report.record_id, report.html_key
    s.delete(report)
    s.commit()
    try:
        d.storage.delete(html_key)
    except Exception:
        log.warning("could not delete %s", html_key, exc_info=True)
    newest = s.scalar(
        select(Report)
        .where(Report.record_table == table, Report.record_id == record_id)
        .order_by(Report.created_at.desc())
    )
    d.data.write_back(table, record_id, newest.report_number if newest else None)
    return "", 204
