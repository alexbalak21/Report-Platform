"""Templates: list, create, draft autosave, publish, validate, render, usage, block upgrade."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request
from rp_shared import events
from rp_shared.auth import (
    require_min_role,
    require_min_role_or_internal,
    require_service_token,
)
from rp_shared.errors import AppError, Conflict, NotFound, Unprocessable, ValidationFailed
from rp_shared.schemas.template_document import (
    TemplateDocument,
    empty_document,
    parse_template_document,
)
from rp_shared.template_render import render_document
from sqlalchemy import func, select

from .. import service
from ..models import Template, TemplateVersion
from . import deps

log = logging.getLogger(__name__)
bp = Blueprint("templates", __name__, url_prefix="/templates")

MAX_PREVIEW_ROWS = 200


class PreconditionRequired(AppError):
    status = 428
    code = "PRECONDITION_REQUIRED"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _blocks():
    return current_app.extensions["blocks"]


def _catalog():
    return current_app.extensions["catalog"]


def _data():
    return current_app.extensions["data"]


def _get(template_id: uuid.UUID) -> Template:
    t = deps.session().get(Template, template_id)
    if t is None:
        raise NotFound("Template not found")
    return t


def _name(value: Any) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValidationFailed("'name' must be 1-120 characters")
    return name


def _description(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 500:
        raise ValidationFailed("'description' must be at most 500 characters")
    return text


def _expected_revision() -> int:
    header = request.headers.get("If-Match")
    if header is None:
        raise PreconditionRequired("Send the draft revision you edited in an If-Match header")
    try:
        return int(header.strip('"'))
    except ValueError:
        raise ValidationFailed("If-Match must be the draft revision number") from None


def _optional_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValidationFailed("Expected a JSON object body")
    return body


def _publish_event(type_: str, data: dict) -> None:
    redis = current_app.extensions.get("redis")
    if redis is None:
        return
    try:
        events.publish(redis, type_, data, g.get("user_id"))
    except Exception:  # best-effort: the change is already committed
        log.warning("could not publish %s", type_, exc_info=True)


def _summary(t: Template) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "description": t.description,
        "status": t.status,
        "draft_revision": t.draft_revision,
        "published_version": t.published_version,
        "has_unpublished_changes": t.published_hash != t.draft_hash,
        "outdated_blocks": len(t.outdated_blocks or []),
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _full(t: Template) -> dict[str, Any]:
    return {
        **_summary(t),
        "document": t.draft_document,
        "outdated_block_list": t.outdated_blocks or [],
        "created_by": str(t.created_by) if t.created_by else None,
    }


def _store_draft(t: Template, doc: TemplateDocument) -> bool:
    """Save `doc` as the draft. Returns False (and changes nothing) when it is identical."""
    digest = service.content_hash(doc)
    if digest == t.draft_hash:
        return False
    t.draft_document = doc.model_dump(mode="json")
    t.draft_hash = digest
    t.draft_revision += 1
    service.sync_usage(deps.session(), t, "draft", doc)
    service.prune_outdated(t, doc)
    return True


def _check_revision(t: Template) -> None:
    if t.status == "archived":
        raise Conflict("This template is archived; restore it first", code="TEMPLATE_ARCHIVED")
    expected = _expected_revision()
    if t.draft_revision != expected:
        raise Conflict(
            f"The template changed (now revision {t.draft_revision}); reload it before saving",
            details=[_full(t)],
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@bp.get("")
@require_service_token
@require_min_role("viewer")
def list_templates():
    s = deps.session()
    page, size = deps.page_params()
    q = select(Template)
    status = request.args.get("status")
    if status in {"draft", "published", "archived"}:
        q = q.where(Template.status == status)
    elif status != "all":
        q = q.where(Template.status != "archived")
    if term := request.args.get("q", "").strip():
        like = func.lower(Template.name).contains(term.lower(), autoescape=True)
        q = q.where(like | func.lower(Template.description).contains(term.lower(), autoescape=True))
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(
        q.order_by(Template.updated_at.desc(), Template.name).limit(size).offset((page - 1) * size)
    ).all()
    return jsonify(deps.page_response([_summary(t) for t in rows], total, page, size))


@bp.get("/<uuid:template_id>")
@require_service_token
@require_min_role("viewer")
def get_template(template_id):
    return jsonify(_full(_get(template_id)))


@bp.get("/<uuid:template_id>/versions")
@require_service_token
@require_min_role("viewer")
def list_versions(template_id):
    t = _get(template_id)
    return jsonify(
        {
            "items": [
                {
                    "version": v.version,
                    "created_by": str(v.created_by) if v.created_by else None,
                    "created_at": v.created_at,
                    "content_hash": v.content_hash[:12],
                    "is_current": v.version == t.published_version,
                }
                for v in reversed(t.versions)
            ]
        }
    )


@bp.get("/<uuid:template_id>/versions/<int:number>")
@require_service_token
@require_min_role_or_internal("viewer")
def get_version(template_id, number):
    """The pinned snapshot a report is generated from (also read by the report service)."""
    t = _get(template_id)
    v = next((v for v in t.versions if v.version == number), None)
    if v is None:
        raise NotFound("Template version not found")
    return jsonify(
        {
            "template_id": str(t.id),
            "name": t.name,
            "version": v.version,
            "document": v.document,
            "content_hash": v.content_hash,
            "created_at": v.created_at,
        }
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
@bp.post("")
@require_service_token
@require_min_role("designer")
def create_template():
    body = deps.json_body()
    doc = parse_template_document(body.get("document") or empty_document())
    t = Template(
        id=uuid.uuid4(),
        name=_name(body.get("name")),
        description=_description(body.get("description")),
        status="draft",
        draft_document=doc.model_dump(mode="json"),
        draft_revision=1,
        draft_hash=service.content_hash(doc),
        outdated_blocks=[],
        created_by=deps.user_uuid(),
    )
    s = deps.session()
    s.add(t)
    service.sync_usage(s, t, "draft", doc)
    s.commit()
    return jsonify(_full(t)), 201


@bp.patch("/<uuid:template_id>")
@require_service_token
@require_min_role("designer")
def update_metadata(template_id):
    t = _get(template_id)
    body = deps.json_body()
    if "name" in body:
        t.name = _name(body["name"])
    if "description" in body:
        t.description = _description(body["description"])
    deps.session().commit()
    return jsonify(_summary(t))


@bp.put("/<uuid:template_id>/draft")
@require_service_token
@require_min_role("designer")
def save_draft(template_id):
    """Autosave target. Body: {document, name?, description?}; header If-Match: <revision>."""
    t = _get(template_id)
    _check_revision(t)
    body = deps.json_body()
    doc = parse_template_document(body.get("document"))
    if "name" in body:
        t.name = _name(body["name"])
    if "description" in body:
        t.description = _description(body["description"])
    _store_draft(t, doc)
    deps.session().commit()
    return jsonify(_full(t))


@bp.post("/<uuid:template_id>/duplicate")
@require_service_token
@require_min_role("designer")
def duplicate_template(template_id):
    src = _get(template_id)
    body = request.get_json(silent=True) or {}
    doc = parse_template_document(src.draft_document)
    t = Template(
        id=uuid.uuid4(),
        name=_name(body.get("name") or f"{src.name} (copy)"),
        description=src.description,
        status="draft",
        draft_document=doc.model_dump(mode="json"),
        draft_revision=1,
        draft_hash=service.content_hash(doc),
        outdated_blocks=list(src.outdated_blocks or []),
        created_by=deps.user_uuid(),
    )
    s = deps.session()
    s.add(t)
    service.sync_usage(s, t, "draft", doc)
    s.commit()
    return jsonify(_full(t)), 201


@bp.post("/<uuid:template_id>/archive")
@require_service_token
@require_min_role("designer")
def archive_template(template_id):
    t = _get(template_id)
    t.status = "archived"
    deps.session().commit()
    return jsonify(_summary(t))


@bp.post("/<uuid:template_id>/restore")
@require_service_token
@require_min_role("designer")
def restore_template(template_id):
    t = _get(template_id)
    t.status = "published" if t.published_version else "draft"
    deps.session().commit()
    return jsonify(_summary(t))


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
@bp.post("/<uuid:template_id>/publish")
@require_service_token
@require_min_role("designer")
def publish_template(template_id):
    """Snapshot the draft as the next immutable version, if it validates without errors."""
    t = _get(template_id)
    if t.status == "archived":
        raise Conflict("This template is archived; restore it first", code="TEMPLATE_ARCHIVED")
    doc = parse_template_document(t.draft_document)
    result = service.summarize(service.validate_document(doc, _blocks(), _catalog().fetch()))
    if not result["ok"]:
        raise Unprocessable(
            f"The template has {result['errors']} error(s) to fix before publishing",
            code="TEMPLATE_INVALID",
            details=result["issues"],
        )
    if t.published_hash == t.draft_hash:
        return jsonify({**_full(t), "unchanged": True})

    version = (t.published_version or 0) + 1
    t.versions.append(
        TemplateVersion(
            version=version,
            document=doc.model_dump(mode="json"),
            content_hash=t.draft_hash,
            created_by=deps.user_uuid(),
        )
    )
    t.published_version = version
    t.published_hash = t.draft_hash
    t.status = "published"
    service.sync_usage(deps.session(), t, "published", doc)
    deps.session().commit()
    _publish_event(
        "template.published", {"template_id": str(t.id), "version": version, "name": t.name}
    )
    return jsonify({**_full(t), "unchanged": False, "validation": result})


# ---------------------------------------------------------------------------
# Validate / render (operators may preview; nothing is stored)
# ---------------------------------------------------------------------------
def _document_for(t: Template, body: dict[str, Any]) -> TemplateDocument:
    """Document to work on: a published version, one sent by the builder, or the draft."""
    if body.get("version") is not None:
        try:
            number = int(body["version"])
        except (TypeError, ValueError):
            raise ValidationFailed("'version' must be an integer") from None
        v = next((v for v in t.versions if v.version == number), None)
        if v is None:
            raise NotFound("Template version not found")
        return parse_template_document(v.document)
    if body.get("document") is not None:
        return parse_template_document(body["document"])
    return parse_template_document(t.draft_document)


@bp.post("/<uuid:template_id>/validate")
@require_service_token
@require_min_role("operator")
def validate_template(template_id):
    t = _get(template_id)
    doc = _document_for(t, _optional_body())
    return jsonify(service.summarize(service.validate_document(doc, _blocks(), _catalog().fetch())))


@bp.post("/<uuid:template_id>/render")
@require_service_token
@require_min_role("operator")
def render_template(template_id):
    """Render to A4 HTML.

    Body (all optional): {version | document, mode: preview|print, missing: marker|blank,
    record: {table, id}, report_number, max_rows}. Without `record` the keywords are shown
    literally ({{lot}}) so the designer sees the layout; with it they are filled with real data.
    """
    t = _get(template_id)
    body = _optional_body()
    mode = body.get("mode", "preview")
    if mode not in {"preview", "print"}:
        raise ValidationFailed("'mode' must be 'preview' or 'print'")
    missing = body.get("missing", "marker")
    if missing not in {"marker", "blank"}:
        raise ValidationFailed("'missing' must be 'marker' or 'blank'")

    doc = _document_for(t, body)
    pins = service.collect_pins(doc, _blocks())

    context = tables = None
    report_number = None
    if (record := body.get("record")) is not None:
        if not isinstance(record, dict) or not record.get("table") or not record.get("id"):
            raise ValidationFailed("'record' must be {table, id}")
        try:
            row = _data().record(str(record["table"]), str(record["id"]))
        except ValueError:
            raise ValidationFailed("'record.id' must be a UUID") from None
        try:
            limit = min(max(int(body.get("max_rows", 25)), 1), MAX_PREVIEW_ROWS)
        except (TypeError, ValueError):
            raise ValidationFailed("'max_rows' must be an integer") from None
        context = row
        tables = {tbl: _data().rows(tbl, limit) for tbl in sorted(service.datasources(doc, pins))}
        report_number = str(body.get("report_number") or row.get("_report_number") or "PREVIEW")

    out = render_document(
        doc,
        pins,
        context=context,
        tables=tables,
        mode=mode,
        missing=missing,
        report_number=report_number,
        title=t.name,
    )
    return jsonify(
        {
            "ok": out.ok,
            "html": out.html,
            "mode": mode,
            "with_data": context is not None,
            "unresolved": out.unresolved,
            "errors": out.errors,
            "missing_blocks": out.missing_blocks,
        }
    )


# ---------------------------------------------------------------------------
# Block usage and upgrades
# ---------------------------------------------------------------------------
@bp.get("/<uuid:template_id>/usage")
@require_service_token
@require_min_role("viewer")
def usage(template_id):
    t = _get(template_id)
    items = []
    for source in ("draft", "published"):
        for block_id, pinned in service.latest_pins(deps.session(), t.id, source).items():
            info = _blocks().summary(block_id)
            latest = info["current_version"] if info else None
            items.append(
                {
                    "source": source,
                    "block_id": str(block_id),
                    "name": info["name"] if info else None,
                    "pinned_version": pinned,
                    "latest_version": latest,
                    "outdated": latest is not None and latest > pinned,
                    "archived": bool(info and info.get("is_archived")),
                }
            )
    return jsonify({"items": items})


@bp.post("/<uuid:template_id>/upgrade-blocks")
@require_service_token
@require_min_role("designer")
def upgrade_blocks(template_id):
    """Move the draft's block instances to the latest block versions.

    Body (optional): {block_ids: [...]} to upgrade only those blocks. Header If-Match applies.
    Instance settings are kept, so the response carries a fresh validation of the result.
    """
    t = _get(template_id)
    _check_revision(t)
    body = _optional_body()
    only = {str(b) for b in body["block_ids"]} if isinstance(body.get("block_ids"), list) else None
    doc = parse_template_document(t.draft_document)
    summaries: dict[str, dict[str, Any] | None] = {}
    changes = []
    for inst in doc.instances():
        bid = str(inst.block_id)
        if only is not None and bid not in only:
            continue
        if bid not in summaries:
            summaries[bid] = _blocks().summary(bid)
        info = summaries[bid]
        if not info or info.get("is_archived"):
            continue
        latest = info["current_version"]
        if latest > inst.block_version:
            changes.append(
                {
                    "instance_id": inst.instance_id,
                    "block_id": bid,
                    "name": info["name"],
                    "from": inst.block_version,
                    "to": latest,
                }
            )
            inst.block_version = latest
    if changes:
        _store_draft(t, doc)
        deps.session().commit()
    validation = service.summarize(service.validate_document(doc, _blocks(), _catalog().fetch()))
    return jsonify({"changes": changes, "template": _full(t), "validation": validation})
