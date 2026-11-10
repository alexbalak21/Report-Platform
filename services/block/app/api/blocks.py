"""Block library: list, read, create, version, duplicate, archive, preview, import/export."""

from __future__ import annotations

import html as html_lib
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request
from rp_shared import events, placeholders
from rp_shared.auth import (
    require_min_role,
    require_min_role_or_internal,
    require_service_token,
)
from rp_shared.css_scope import PREVIEW_SCOPE, scope_css
from rp_shared.errors import AppError, Conflict, Forbidden, NotFound, ValidationFailed
from rp_shared.sanitizer import sanitize_template_html
from rp_shared.schemas.block import CATEGORIES, BlockDefinition, parse_block_definition
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..models import Block, BlockVersion
from ..service import COMPUTED_FIELDS, add_version, build, syntax_issues
from . import deps

log = logging.getLogger(__name__)
bp = Blueprint("blocks", __name__, url_prefix="/blocks")

EXPORT_FORMAT = "rp-block"
EXPORT_VERSION = 1
PREVIEW_BASE_CSS = (
    "html,body{margin:0;padding:0}"
    'body{font:10pt/1.4 "Helvetica Neue",Arial,sans-serif;color:#111;background:#fff;padding:4mm}'
    "table{border-collapse:collapse;width:100%}img{max-width:100%}"
)
PREVIEW_CSP = "default-src 'none'; img-src 'self' data: https:; style-src 'unsafe-inline'"


class PreconditionRequired(AppError):
    status = 428
    code = "PRECONDITION_REQUIRED"


class DefaultBlockReadOnly(Forbidden):
    code = "DEFAULT_BLOCK_READ_ONLY"

    def __init__(self) -> None:
        super().__init__("Default blocks are read-only; duplicate the block to customise it")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _summary(block: Block, version: BlockVersion) -> dict[str, Any]:
    d = version.definition
    return {
        "id": str(block.id),
        "kind": block.kind,
        "slug": block.slug,
        "name": block.name,
        "category": block.category,
        "description": block.description,
        "is_archived": block.is_archived,
        "current_version": block.current_version,
        "default_size_mm": d.get("default_size_mm"),
        "repeatable_region": d.get("repeatable_region", "body"),
        "resizable": d.get("resizable", True),
        "has_loop": bool(d.get("loops")),
        "updated_at": block.updated_at,
    }


def _version_body(version: BlockVersion) -> dict[str, Any]:
    return {
        "version": version.version,
        "definition": version.definition,
        "html_sanitized": version.html_sanitized,
        "css_sanitized": version.css_sanitized,
        "scope_class": version.scope_class,
        "created_by": str(version.created_by) if version.created_by else None,
        "created_at": version.created_at,
    }


def _full(block: Block, version: BlockVersion, warnings: list[str] | None = None) -> dict:
    out = {**_summary(block, version), "owner_id": str(block.owner_id) if block.owner_id else None}
    out["created_at"] = block.created_at
    out["current"] = _version_body(version)
    out["warnings"] = warnings or []
    return out


def _get_block(block_id: uuid.UUID) -> Block:
    block = deps.session().get(Block, block_id)
    if block is None:
        raise NotFound("Block not found")
    return block


def _get_version(block: Block, number: int) -> BlockVersion:
    version = next((v for v in block.versions if v.version == number), None)
    if version is None:
        raise NotFound("Block version not found")
    return version


def _current(block: Block) -> BlockVersion:
    return _get_version(block, block.current_version)


def _publish(type_: str, data: dict) -> None:
    redis = current_app.extensions.get("redis")
    if redis is None:
        return
    try:
        events.publish(redis, type_, data, g.get("user_id"))
    except Exception:  # best-effort: the change is already committed
        log.warning("could not publish %s", type_, exc_info=True)


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------
def _name(value: Any) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValidationFailed("'name' must be 1-120 characters")
    return name


def _category(value: Any) -> str:
    if value not in CATEGORIES:
        raise ValidationFailed(f"'category' must be one of: {', '.join(CATEGORIES)}")
    return value


def _description(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 500:
        raise ValidationFailed("'description' must be at most 500 characters")
    return text


def _expected_version() -> int:
    header = request.headers.get("If-Match")
    if header is None:
        raise PreconditionRequired("Send the block version you edited in an If-Match header")
    try:
        return int(header.strip('"'))
    except ValueError:
        raise ValidationFailed("If-Match must be the block version number") from None


def _editable(block: Block) -> None:
    if block.kind == "default":
        raise DefaultBlockReadOnly()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@bp.get("")
@require_service_token
@require_min_role_or_internal("viewer")
def list_blocks():
    s: Session = deps.session()
    page, size = deps.page_params()
    q = select(Block, BlockVersion).join(
        BlockVersion,
        and_(BlockVersion.block_id == Block.id, BlockVersion.version == Block.current_version),
    )
    if kind := request.args.get("kind"):
        q = q.where(Block.kind == kind)
    if category := request.args.get("category"):
        q = q.where(Block.category == category)
    if request.args.get("include_archived") not in {"1", "true"}:
        q = q.where(Block.is_archived.is_(False))
    if term := request.args.get("q", "").strip():
        like = func.lower(Block.name).contains(term.lower(), autoescape=True)
        q = q.where(like | func.lower(Block.description).contains(term.lower(), autoescape=True))
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.execute(
        q.order_by(Block.kind.desc(), Block.category, Block.name)  # defaults first
        .limit(size)
        .offset((page - 1) * size)
    ).all()
    return jsonify(deps.page_response([_summary(b, v) for b, v in rows], total, page, size))


@bp.get("/<uuid:block_id>")
@require_service_token
@require_min_role_or_internal("viewer")
def get_block(block_id):
    block = _get_block(block_id)
    return jsonify(_full(block, _current(block)))


@bp.get("/<uuid:block_id>/versions")
@require_service_token
@require_min_role_or_internal("viewer")
def list_versions(block_id):
    block = _get_block(block_id)
    return jsonify(
        {
            "items": [
                {
                    "version": v.version,
                    "created_by": str(v.created_by) if v.created_by else None,
                    "created_at": v.created_at,
                    "content_hash": v.content_hash[:12],
                    "is_current": v.version == block.current_version,
                }
                for v in reversed(block.versions)
            ]
        }
    )


@bp.get("/<uuid:block_id>/versions/<int:number>")
@require_service_token
@require_min_role_or_internal("viewer")
def get_version(block_id, number):
    """The pinned snapshot a template renders from (also used by other services)."""
    block = _get_block(block_id)
    body = _version_body(_get_version(block, number))
    return jsonify({"block_id": str(block.id), "name": block.name, "kind": block.kind, **body})


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def _create(
    name: str, category: str, description: str, defn: BlockDefinition
) -> tuple[Block, list[str]]:
    s = deps.session()
    block = Block(
        id=uuid.uuid4(),
        kind="custom",
        name=name,
        category=category,
        description=description,
        owner_id=deps.user_uuid(),
        current_version=0,
    )
    s.add(block)
    _row, built = add_version(s, block, defn, deps.user_uuid())
    s.commit()
    _publish("block.updated", {"block_id": str(block.id), "version": 1, "name": block.name})
    return block, built.warnings


@bp.post("")
@require_service_token
@require_min_role("designer")
def create_block():
    body = deps.json_body()
    defn = parse_block_definition(body.get("definition"))
    block, warnings = _create(
        _name(body.get("name")),
        _category(body.get("category", "other")),
        _description(body.get("description")),
        defn,
    )
    return jsonify(_full(block, _current(block), warnings)), 201


@bp.put("/<uuid:block_id>")
@require_service_token
@require_min_role("designer")
def update_block(block_id):
    expected = _expected_version()
    block = _get_block(block_id)
    _editable(block)
    if block.current_version != expected:
        raise Conflict(
            f"The block changed (now version {block.current_version}); reload it before saving",
            details=[_full(block, _current(block))],
        )
    body = deps.json_body()
    if "name" in body:
        block.name = _name(body["name"])
    if "category" in body:
        block.category = _category(body["category"])
    if "description" in body:
        block.description = _description(body["description"])
    warnings: list[str] = []
    new_version = None
    if "definition" in body:
        new_version, built = add_version(
            deps.session(), block, parse_block_definition(body["definition"]), deps.user_uuid()
        )
        warnings = built.warnings
    deps.session().commit()
    if new_version is not None:
        _publish(
            "block.updated",
            {"block_id": str(block.id), "version": new_version.version, "name": block.name},
        )
    return jsonify(_full(block, _current(block), warnings))


@bp.post("/<uuid:block_id>/duplicate")
@require_service_token
@require_min_role("designer")
def duplicate_block(block_id):
    source = _get_block(block_id)
    body = request.get_json(silent=True) or {}
    name = _name(body.get("name") or f"{source.name} (copy)")
    defn = parse_block_definition(_current(source).definition)
    block, warnings = _create(name, source.category, source.description, defn)
    return jsonify(_full(block, _current(block), warnings)), 201


def _set_archived(block_id: uuid.UUID, archived: bool):
    block = _get_block(block_id)
    _editable(block)
    block.is_archived = archived
    deps.session().commit()
    return jsonify(_summary(block, _current(block)))


@bp.post("/<uuid:block_id>/archive")
@require_service_token
@require_min_role("designer")
def archive_block(block_id):
    return _set_archived(block_id, True)


@bp.post("/<uuid:block_id>/restore")
@require_service_token
@require_min_role("designer")
def restore_block(block_id):
    return _set_archived(block_id, False)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
def _prop_defaults(defn: BlockDefinition) -> dict[str, Any]:
    return {k: v.default for k, v in defn.props_schema.items() if v.default is not None}


@bp.post("/preview")
@require_service_token
@require_min_role("operator")
def preview():
    """Render a draft definition with sample data. Never stores anything.

    Body: {definition, props?: {...}, context?: {column: value, rows: [...]}}.
    Problems the author can fix while typing come back as `errors` with HTTP 200.
    """
    body = deps.json_body()
    context = body.get("context") or {}
    props = body.get("props") or {}
    if not isinstance(context, dict) or not isinstance(props, dict):
        raise ValidationFailed("'context' and 'props' must be objects")
    try:
        defn = parse_block_definition(body.get("definition"))
    except ValidationFailed as exc:
        return jsonify(_failed(exc.details))
    html = sanitize_template_html(defn.html)
    if issues := syntax_issues(html):
        return jsonify(_failed(issues))

    built = build(defn, PREVIEW_SCOPE)
    ctx = {**context, "props": {**_prop_defaults(defn), **props}}
    result = placeholders.render(html, ctx, missing="marker")
    css = scope_css(defn.css, PREVIEW_SCOPE).css
    document = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{html_lib.escape(PREVIEW_CSP)}">'
        f"<style>{PREVIEW_BASE_CSS}</style><style>{css}</style></head>"
        f'<body><section class="blk {PREVIEW_SCOPE}">{result.text}</section></body></html>'
    )
    return jsonify(
        {
            "ok": True,
            "html": result.text,
            "css": css,
            "document": document,
            "placeholders": built.placeholders,
            "loops": built.loops,
            "loop_columns": built.loop_columns,
            "unresolved": result.unresolved,
            "errors": result.errors,
            "warnings": built.warnings,
        }
    )


def _failed(errors: list[dict]) -> dict:
    return {"ok": False, "html": "", "css": "", "document": "", "errors": errors, "warnings": []}


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@bp.get("/<uuid:block_id>/export")
@require_service_token
@require_min_role_or_internal("viewer")
def export_block(block_id):
    block = _get_block(block_id)
    current = _current(block)
    authored = {k: v for k, v in current.definition.items() if k not in COMPUTED_FIELDS}
    payload = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "name": block.name,
        "category": block.category,
        "description": block.description,
        "definition": authored,
    }
    resp = jsonify(payload)
    filename = _FILENAME_UNSAFE.sub("-", block.name).strip("-") or "block"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}.block.json"'
    return resp


@bp.post("/import")
@require_service_token
@require_min_role("designer")
def import_block():
    body = deps.json_body()
    if body.get("format") != EXPORT_FORMAT:
        raise ValidationFailed("This is not an exported block file")
    if body.get("format_version") != EXPORT_VERSION:
        raise ValidationFailed(f"Unsupported block file version: {body.get('format_version')}")
    defn = parse_block_definition(body.get("definition"))
    block, warnings = _create(
        _name(body.get("name")),
        _category(body.get("category", "other")),
        _description(body.get("description")),
        defn,
    )
    return jsonify(_full(block, _current(block), warnings)), 201
