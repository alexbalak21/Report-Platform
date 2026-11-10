"""Browse imported data and expose the placeholder catalog."""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request
from rp_shared.auth import require_min_role, require_service_token
from rp_shared.errors import NotFound, ValidationFailed
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from ..coerce import CoerceError, coerce
from ..dynamic import SYSTEM_COLUMNS, load_table
from ..models import DataColumn, DataTable
from . import deps

bp = Blueprint("tables", __name__, url_prefix="/ingestion")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
BUILTINS = ["today", "now", "report_number", "page", "pages"]


def _table(name: str):
    if not NAME_RE.match(name):
        raise NotFound("Table not found")
    loaded = load_table(deps.session(), deps.session().get_bind(), name)
    if loaded is None:
        raise NotFound("Table not found")
    return loaded


def _columns(s: Session, dt: DataTable) -> list[DataColumn]:
    return list(
        s.scalars(
            select(DataColumn).where(DataColumn.table_id == dt.id).order_by(DataColumn.position)
        )
    )


@bp.get("/tables")
@require_service_token
@require_min_role("viewer")
def list_tables():
    s = deps.session()
    out = []
    for dt in s.scalars(select(DataTable).order_by(DataTable.name)):
        _, table = load_table(s, s.get_bind(), dt.name)
        out.append(
            {
                "name": dt.name,
                "label": dt.label,
                "key_columns": dt.key_columns,
                "row_count": s.scalar(select(func.count()).select_from(table)),
                "columns": [
                    {"name": c.name, "label": c.label, "type": c.type} for c in _columns(s, dt)
                ],
            }
        )
    return jsonify({"items": out})


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@bp.get("/tables/<name>/rows")
@require_service_token
@require_min_role("viewer")
def list_rows(name):
    s = deps.session()
    dt, table = _table(name)
    types = {c.name: c.type for c in _columns(s, dt)}
    sortable = {*types, *SYSTEM_COLUMNS}

    q = select(table)
    for arg, raw in request.args.items():
        if not arg.startswith("eq."):
            continue
        col = arg[3:]
        if col not in types:
            raise ValidationFailed(f"Unknown column '{col}'")
        try:
            value = coerce(raw, types[col])
        except CoerceError as exc:
            raise ValidationFailed(f"Filter on '{col}': {exc}") from exc
        q = q.where(table.c[col].is_(None) if value is None else table.c[col] == value)
    if term := request.args.get("q", "").strip():
        pattern = f"%{_escape_like(term)}%"
        text_cols = [table.c[n] for n, t in types.items() if t == "string"]
        if text_cols:
            q = q.where(or_(*[cast(c, Text).ilike(pattern, escape="\\") for c in text_cols]))

    order = []
    for part in filter(None, request.args.get("sort", "-_created_at").split(",")):
        desc = part.startswith("-")
        col = part.lstrip("-+ ")
        if col not in sortable:
            raise ValidationFailed(f"Cannot sort by '{col}'")
        order.append(table.c[col].desc() if desc else table.c[col].asc())
    order.append(table.c._id)  # stable paging

    page, size = deps.page_params()
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.execute(q.order_by(*order).limit(size).offset((page - 1) * size)).mappings().all()
    return jsonify(deps.page_response([dict(r) for r in rows], total, page, size))


@bp.get("/tables/<name>/rows/<uuid:row_id>")
@require_service_token
@require_min_role("viewer")
def get_row(name, row_id):
    s = deps.session()
    _, table = _table(name)
    row = s.execute(select(table).where(table.c._id == row_id)).mappings().first()
    if row is None:
        raise NotFound("Row not found")
    return jsonify(dict(row))


@bp.patch("/tables/<name>/rows/<uuid:row_id>")
@require_service_token  # internal: the gateway never routes this to browsers
def patch_row(name, row_id):
    """Only the report number can be written back (by the report service)."""
    body = deps.json_body()
    if set(body) != {"_report_number"}:
        raise ValidationFailed("Only '_report_number' can be updated")
    number = body["_report_number"]
    if number is not None and (not isinstance(number, str) or len(number) > 40):
        raise ValidationFailed("'_report_number' must be a string of at most 40 characters")
    s = deps.session()
    _, table = _table(name)
    result = s.execute(table.update().where(table.c._id == row_id).values(_report_number=number))
    if result.rowcount == 0:
        raise NotFound("Row not found")
    s.commit()
    return jsonify({"id": str(row_id), "_report_number": number})


@bp.get("/placeholders")
@require_service_token
@require_min_role("viewer")
def placeholders():
    """Every `{{keyword}}` a template may use, with where its value comes from."""
    s = deps.session()
    items = []
    for dt in s.scalars(select(DataTable).order_by(DataTable.name)):
        for c in _columns(s, dt):
            items.append(
                {
                    "key": c.name,
                    "qualified_key": f"{dt.name}.{c.name}",
                    "table": dt.name,
                    "column": c.name,
                    "label": c.label,
                    "type": c.type,
                }
            )
    return jsonify({"items": items, "builtins": BUILTINS})
