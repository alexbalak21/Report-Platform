"""Turn (published template version + one data record) into a numbered draft report.

Order matters here:
  1. Everything that talks to other services happens first (template, blocks, record, loop rows),
     so no database transaction or counter lock is held during network calls.
  2. Then one short transaction: take the number, render (pure CPU), store the HTML, insert.
     If anything fails the transaction rolls back, and the number goes back with it.
  3. Only after the commit: write the number back to the source row (best effort).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from rp_shared.errors import Conflict, Unprocessable
from rp_shared.schemas.template_document import TemplateDocument, parse_template_document
from rp_shared.storage import Storage
from rp_shared.template_render import Pins, collect_pins, datasources, render_document
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .clients import Actor, BlockDirectory, DataSource, TemplateDirectory
from .config import ReportConfig
from .models import Report, utcnow
from .numbering import next_number

log = logging.getLogger(__name__)

# What a keyword with no value looks like in a draft: [[key?]] (missing) / [[key!]] (error).
MARKER_RE = re.compile(r"\[\[([a-z_][a-z0-9_.]*)[?!]\]\]")


@dataclass
class Deps:
    templates: TemplateDirectory
    blocks: BlockDirectory
    data: DataSource
    storage: Storage
    config: ReportConfig


@dataclass
class TemplateInputs:
    """Everything about a published template version that doesn't depend on the record."""

    template_id: uuid.UUID
    version: int
    name: str
    doc: TemplateDocument
    pins: Pins
    tables: dict[str, list[dict]]


def html_key(report_id: uuid.UUID) -> str:
    return f"reports/{report_id}/report.html"


def pdf_key(report_id: uuid.UUID) -> str:
    return f"reports/{report_id}/report.pdf"


def today(config: ReportConfig) -> date:
    return datetime.now(ZoneInfo(config.timezone)).date()


def load_template(
    deps: Deps, template_id: uuid.UUID, version: int | None, actor: Actor
) -> TemplateInputs:
    number, body = deps.templates.version(template_id, version, actor)
    doc = parse_template_document(body["document"])
    pins = collect_pins(doc, deps.blocks)
    missing = [
        {"block_id": str(i.block_id), "block_version": i.block_version}
        for i in doc.instances()
        if (str(i.block_id), i.block_version) not in pins
    ]
    if missing:
        raise Unprocessable(
            "Some blocks of this template version no longer exist",
            code="BLOCK_MISSING",
            details=missing,
        )
    tables = {
        table: deps.data.rows(table, deps.config.max_rows_per_table, actor)
        for table in sorted(datasources(doc, pins))
    }
    return TemplateInputs(template_id, number, body.get("name", ""), doc, pins, tables)


def _render(tpl: TemplateInputs, record: dict, number: str) -> tuple[str, list[str]]:
    out = render_document(
        tpl.doc,
        tpl.pins,
        context=record,
        tables=tpl.tables,
        mode="print",
        missing=tpl.doc.settings.missing_policy,
        report_number=number,
        title=f"{tpl.name} {number}".strip(),
    )
    if out.errors or out.missing_blocks:
        raise Unprocessable(
            "The template could not be rendered with this record",
            code="RENDER_FAILED",
            details=[*out.errors, *out.missing_blocks],
        )
    if out.unresolved and tpl.doc.settings.missing_policy == "error":
        raise Unprocessable(
            f"{len(out.unresolved)} keyword(s) have no value for this record",
            code="REPORT_UNRESOLVED_KEYS",
            details=[{"key": k} for k in out.unresolved],
        )
    return out.html, out.unresolved


def create_report(
    deps: Deps,
    session: Session,
    tpl: TemplateInputs,
    table: str,
    record_id: uuid.UUID,
    actor: Actor,
    day: date | None = None,
) -> Report:
    record = deps.data.record(table, record_id, actor)  # network first, no transaction yet
    day = day or today(deps.config)
    report_id = uuid.uuid4()
    key = html_key(report_id)
    stored = False
    try:
        number = next_number(session, day)
        html, unresolved = _render(tpl, record, number)
        deps.storage.put(key, html.encode("utf-8"))
        stored = True
        user = uuid.UUID(actor.user_id) if actor.user_id else None
        report = Report(
            id=report_id,
            report_number=number,
            report_date=day,
            template_id=tpl.template_id,
            template_version=tpl.version,
            template_name=tpl.name,
            record_table=table,
            record_id=record_id,
            html_key=key,
            unresolved_keys=unresolved,
            created_by=user,
        )
        session.add(report)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _discard(deps, key, stored)
        existing = session.scalar(
            select(Report).where(
                Report.template_id == tpl.template_id,
                Report.record_table == table,
                Report.record_id == record_id,
            )
        )
        if existing is None:  # some other constraint: don't hide it
            raise
        raise Conflict(
            f"A report already exists for this record ({existing.report_number})",
            code="REPORT_EXISTS",
            details=[{"report_id": str(existing.id), "report_number": existing.report_number}],
        ) from exc
    except Exception:
        session.rollback()
        _discard(deps, key, stored)
        raise
    sync_record(deps, session, report)
    return report


def regenerate_report(
    deps: Deps, session: Session, report: Report, tpl: TemplateInputs, actor: Actor
) -> Report:
    """Render again into the same report (same id and number); manual edits are discarded."""
    record = deps.data.record(report.record_table, report.record_id, actor)
    html, unresolved = _render(tpl, record, report.report_number)
    deps.storage.put(report.html_key, html.encode("utf-8"))
    report.template_version = tpl.version
    report.template_name = tpl.name
    report.unresolved_keys = unresolved
    report.updated_at = utcnow()
    session.commit()
    sync_record(deps, session, report)
    return report


def sync_record(deps: Deps, session: Session, report: Report) -> None:
    """Write the number back to the source row; remember whether it worked."""
    ok = deps.data.write_back(report.record_table, report.record_id, report.report_number)
    if ok != report.record_synced:
        report.record_synced = ok
        session.commit()
    if not ok:
        log.warning("report %s: number not written back to the record", report.report_number)


def leftover_keys(html: str) -> list[str]:
    """Keywords still shown as [[key?]] in the (possibly hand-edited) report."""
    seen: list[str] = []
    for key in MARKER_RE.findall(html):
        if key not in seen:
            seen.append(key)
    return seen


def _discard(deps: Deps, key: str, stored: bool) -> None:
    if stored:
        try:
            deps.storage.delete(key)
        except Exception:
            log.warning("could not delete orphaned object %s", key, exc_info=True)
