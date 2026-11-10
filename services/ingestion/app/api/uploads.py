"""Upload a workbook, preview it, and start an import."""

from __future__ import annotations

import os
import re
import uuid

from flask import Blueprint, current_app, jsonify, request
from rp_shared.auth import require_min_role, require_service_token
from rp_shared.errors import Conflict, NotFound, ValidationFailed
from sqlalchemy import func, select

from ..engine import run_import
from ..excel import (
    check_xlsx,
    find_sheet,
    norm_header,
    preview_sheet,
    read_headers,
    sheet_names,
)
from ..models import ImportProfile, ImportRun, Upload
from ..profile import parse_definition, score_profile, used_headers
from . import deps
from .runs import serialize_run

bp = Blueprint("uploads", __name__, url_prefix="/ingestion")


def serialize_upload(u: Upload, **extra) -> dict:
    return {
        "id": str(u.id),
        "filename": u.filename,
        "size_bytes": u.size_bytes,
        "status": u.status,
        "profile_id": str(u.profile_id) if u.profile_id else None,
        "uploaded_by": str(u.uploaded_by) if u.uploaded_by else None,
        "created_at": u.created_at,
        **extra,
    }


def _clean_filename(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    if not base.lower().endswith(".xlsx"):
        raise ValidationFailed("Only .xlsx files are accepted")
    return base[:255]


def _get_upload(upload_id: uuid.UUID) -> Upload:
    upload = deps.session().get(Upload, upload_id)
    if upload is None:
        raise NotFound("Upload not found")
    return upload


@bp.post("/uploads")
@require_service_token
@require_min_role("operator")
def create_upload():
    file = request.files.get("file")
    if file is None:
        raise ValidationFailed("Send the workbook as multipart field 'file'")
    config = deps.cfg()
    filename = _clean_filename(file.filename)
    data = file.read(config.max_upload_bytes + 1)
    check_xlsx(data, config)
    sheets = sheet_names(data, config)  # also proves the workbook opens

    upload_id = uuid.uuid4()
    key = f"uploads/{upload_id}.xlsx"
    deps.storage().put(key, data)
    s = deps.session()
    upload = Upload(
        id=upload_id,
        filename=filename,
        storage_key=key,
        size_bytes=len(data),
        uploaded_by=deps.user_uuid(),
    )
    s.add(upload)
    s.commit()
    return jsonify(serialize_upload(upload, sheets=sheets)), 201


@bp.get("/uploads")
@require_service_token
@require_min_role("viewer")
def list_uploads():
    s = deps.session()
    page, size = deps.page_params()
    total = s.scalar(select(func.count()).select_from(Upload)) or 0
    rows = s.scalars(
        select(Upload).order_by(Upload.created_at.desc()).limit(size).offset((page - 1) * size)
    ).all()
    return jsonify(deps.page_response([serialize_upload(u) for u in rows], total, page, size))


@bp.get("/uploads/<uuid:upload_id>")
@require_service_token
@require_min_role("viewer")
def get_upload(upload_id):
    return jsonify(serialize_upload(_get_upload(upload_id)))


@bp.get("/uploads/<uuid:upload_id>/preview")
@require_service_token
@require_min_role("viewer")
def preview(upload_id):
    upload = _get_upload(upload_id)
    config = deps.cfg()
    data = deps.storage().get(upload.storage_key)
    names = sheet_names(data, config)
    try:
        header_row = max(1, int(request.args.get("header_row", 1)))
    except ValueError:
        raise ValidationFailed("'header_row' must be an integer") from None

    wanted = request.args.get("sheet")
    chosen = find_sheet(names, wanted) if wanted else (names[0] if names else None)
    if chosen is None:
        raise NotFound(f"Sheet '{wanted}' not found" if wanted else "The workbook has no sheets")

    headers_cache: dict[int, dict[str, list[str]]] = {}

    def headers_for(row: int) -> dict[str, list[str]]:
        if row not in headers_cache:
            headers_cache[row] = {n: read_headers(data, n, row).labels() for n in names}
        return headers_cache[row]

    suggestions = []
    for p in deps.session().scalars(select(ImportProfile)).all():
        defn = parse_definition(p.definition)
        fit = score_profile(defn, headers_for(defn.header_row))
        if fit["sheet"] is None:
            continue
        sheet_headers = headers_for(defn.header_row)[fit["sheet"]]
        used = used_headers(defn)
        suggestions.append(
            {
                "id": str(p.id),
                "name": p.name,
                "version": p.version,
                **fit,
                "suggested": fit["score"] >= 0.8,
                "unmapped_headers": [h for h in sheet_headers if norm_header(h) not in used],
            }
        )
    suggestions.sort(key=lambda x: -x["score"])
    return jsonify(
        {
            "upload": serialize_upload(upload),
            "sheets": [{"name": n, "headers": headers_for(1)[n]} for n in names],
            "preview": preview_sheet(data, chosen, config, header_row),
            "suggested_profiles": suggestions,
        }
    )


@bp.post("/uploads/<uuid:upload_id>/import")
@require_service_token
@require_min_role("operator")
def start_import(upload_id):
    body = deps.json_body()
    try:
        profile_id = uuid.UUID(str(body.get("profile_id")))
    except ValueError:
        raise ValidationFailed("'profile_id' is required") from None
    dry_run = bool(body.get("dry_run", False))

    s = deps.session()
    upload = _get_upload(upload_id)
    profile = s.get(ImportProfile, profile_id)
    if profile is None:
        raise NotFound("Profile not found")
    busy = s.scalar(
        select(func.count())
        .select_from(ImportRun)
        .where(ImportRun.upload_id == upload.id, ImportRun.status.in_(("queued", "running")))
    )
    if busy:
        raise Conflict("An import of this file is already running")
    defn = parse_definition(profile.definition)  # refuse a profile that went stale

    run = ImportRun(
        upload_id=upload.id,
        profile_id=profile.id,
        profile_version=profile.version,
        profile_snapshot=defn.model_dump(mode="json"),
        dry_run=dry_run,
        created_by=deps.user_uuid(),
    )
    s.add(run)
    s.commit()
    run_id = run.id

    app = current_app._get_current_object()
    app.extensions["jobs"].submit(
        run_import,
        app.extensions["db_sessionmaker"],
        app.extensions["db_engine"],
        app.extensions["storage"],
        deps.cfg(),
        run_id,
        app.extensions.get("redis"),
    )
    s.expire_all()  # a synchronous job (tests) has already updated the row
    return jsonify(serialize_run(s.get(ImportRun, run_id))), 202
