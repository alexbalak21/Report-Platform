"""Import profile CRUD, plus validation against a real file."""

from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request
from rp_shared.auth import require_min_role, require_service_token
from rp_shared.errors import AppError, Conflict, NotFound, ValidationFailed
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..engine import ProfileMismatch, read_workbook
from ..models import ImportProfile, Upload
from ..profile import parse_definition
from . import deps

bp = Blueprint("profiles", __name__, url_prefix="/ingestion")


class PreconditionRequired(AppError):
    status = 428
    code = "PRECONDITION_REQUIRED"


def serialize_profile(p: ImportProfile) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "version": p.version,
        "definition": p.definition,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _get(profile_id: uuid.UUID) -> ImportProfile:
    p = deps.session().get(ImportProfile, profile_id)
    if p is None:
        raise NotFound("Profile not found")
    return p


def _name(body: dict) -> str:
    name = str(body.get("name", "")).strip()
    if not 1 <= len(name) <= 120:
        raise ValidationFailed("'name' must be 1-120 characters")
    return name


@bp.get("/profiles")
@require_service_token
@require_min_role("viewer")
def list_profiles():
    s = deps.session()
    page, size = deps.page_params()
    q = select(ImportProfile)
    if term := request.args.get("q", "").strip():
        q = q.where(func.lower(ImportProfile.name).contains(term.lower(), autoescape=True))
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(q.order_by(ImportProfile.name).limit(size).offset((page - 1) * size)).all()
    return jsonify(deps.page_response([serialize_profile(p) for p in rows], total, page, size))


@bp.post("/profiles")
@require_service_token
@require_min_role("designer")
def create_profile():
    body = deps.json_body()
    defn = parse_definition(body.get("definition"))
    s = deps.session()
    profile = ImportProfile(
        name=_name(body), definition=defn.model_dump(mode="json"), created_by=deps.user_uuid()
    )
    s.add(profile)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise Conflict("A profile with this name already exists") from None
    return jsonify(serialize_profile(profile)), 201


@bp.get("/profiles/<uuid:profile_id>")
@require_service_token
@require_min_role("viewer")
def get_profile(profile_id):
    return jsonify(serialize_profile(_get(profile_id)))


@bp.put("/profiles/<uuid:profile_id>")
@require_service_token
@require_min_role("designer")
def update_profile(profile_id):
    header = request.headers.get("If-Match")
    if header is None:
        raise PreconditionRequired("Send the profile version you edited in an If-Match header")
    try:
        expected = int(header.strip('"'))
    except ValueError:
        raise ValidationFailed("If-Match must be the profile version number") from None
    s = deps.session()
    profile = _get(profile_id)
    if profile.version != expected:
        raise Conflict(
            f"The profile changed (now version {profile.version}); reload it before saving",
            details=[serialize_profile(profile)],
        )
    body = deps.json_body()
    if "name" in body:
        profile.name = _name(body)
    if "definition" in body:
        profile.definition = parse_definition(body["definition"]).model_dump(mode="json")
    profile.version += 1
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        raise Conflict("A profile with this name already exists") from None
    return jsonify(serialize_profile(profile))


@bp.delete("/profiles/<uuid:profile_id>")
@require_service_token
@require_min_role("designer")
def delete_profile(profile_id):
    s = deps.session()
    s.delete(_get(profile_id))
    s.commit()
    return "", 204


def _check_against_upload(definition, upload_id) -> dict:
    s = deps.session()
    upload = s.get(Upload, upload_id)
    if upload is None:
        raise NotFound("Upload not found")
    config = deps.cfg()
    data = deps.storage().get(upload.storage_key)
    try:
        result = read_workbook(data, definition, config)
    except ProfileMismatch as exc:
        return {"valid": False, "message": exc.message, "problems": exc.details}
    errors = sorted(result.errors, key=lambda e: e["level"] != "error")
    return {
        "valid": result.error_count == 0,
        "rows_total": result.rows_total,
        "rows_ok": result.rows_ok,
        "rows_failed": result.rows_failed,
        "errors": errors[:50],
        "errors_total": len(errors),
    }


@bp.post("/profiles/validate")
@require_service_token
@require_min_role("designer")
def validate_draft():
    """Check a draft definition (and optionally dry-run it against an uploaded file)."""
    body = deps.json_body()
    defn = parse_definition(body.get("definition"))
    out: dict = {"valid": True}
    if body.get("upload_id"):
        try:
            upload_id = uuid.UUID(str(body["upload_id"]))
        except ValueError:
            raise ValidationFailed("'upload_id' is not valid") from None
        out = _check_against_upload(defn, upload_id)
    return jsonify(out)


@bp.post("/profiles/<uuid:profile_id>/validate")
@require_service_token
@require_min_role("designer")
def validate_saved(profile_id):
    body = request.get_json(silent=True) or {}
    defn = parse_definition(_get(profile_id).definition)
    try:
        upload_id = uuid.UUID(str(body.get("upload_id")))
    except ValueError:
        raise ValidationFailed("'upload_id' is required") from None
    return jsonify(_check_against_upload(defn, upload_id))
