"""The import engine: workbook + profile -> validated records -> upserted rows.

Two phases so that nothing is written when the file can't be trusted:
  1. read_workbook(): pure, no database. Resolves headers, applies lookup / coercion /
     operations, collects every problem as {row, column, message, value, level}.
  2. write_records(): one transaction for all tables (DDL + upserts), atomic on Postgres.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from rp_shared import events
from rp_shared.errors import AppError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .coerce import CoerceError, apply_operations, coerce
from .config import IngestionConfig
from .dynamic import (
    WRITE_LOCK_KEY,
    ColSpec,
    count_rows,
    ensure_table,
    now,
    upsert,
)
from .excel import (
    Headers,
    build_lookup,
    find_sheet,
    iter_sheet_rows,
    lookup_key,
    norm_header,
    read_headers,
    sheet_names,
)
from .models import ImportRun, Upload, utcnow
from .profile import (
    ProfileDefinition,
    TableRule,
    parse_definition,
    resolve_sheet,
    used_headers,
)

log = logging.getLogger(__name__)


class ProfileMismatch(AppError):
    """The profile can't be applied to this workbook (missing sheet / column, bad lookup)."""

    status = 422
    code = "PROFILE_MISMATCH"


class ImportAborted(AppError):
    status = 422
    code = "IMPORT_ABORTED"


@dataclass
class ReadResult:
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    rows_total: int = 0
    rows_ok: int = 0
    rows_failed: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.errors if e["level"] == "error")


def _issue(level: str, message: str, row=None, column=None, value=None) -> dict[str, Any]:
    shown = None if value is None else str(value)[:100]
    return {"level": level, "row": row, "column": column, "message": message, "value": shown}


def _row_hash(rec: dict[str, Any]) -> str:
    payload = json.dumps(sorted((k, str(v)) for k, v in rec.items()), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def read_workbook(data: bytes, defn: ProfileDefinition, cfg: IngestionConfig) -> ReadResult:
    names = sheet_names(data, cfg)
    result = ReadResult(records={t.table: [] for t in defn.tables})

    # Group the profile's tables by worksheet (usually all on one).
    groups: dict[str, list[TableRule]] = {}
    for t in defn.tables:
        wanted = resolve_sheet(defn, t)
        sheet = find_sheet(names, wanted)
        if sheet is None:
            raise ProfileMismatch(
                f"Sheet '{wanted}' not found. Sheets in this file: {', '.join(names)}"
            )
        groups.setdefault(sheet, []).append(t)

    lookups: dict[tuple, tuple[dict, set]] = {}
    for sheet, tables in groups.items():
        headers = read_headers(data, sheet, defn.header_row)
        col_index = _resolve_columns(sheet, headers, tables)
        for c in (c for t in tables for c in t.columns if c.lookup):
            lk = c.lookup
            sig = (lk.sheet, lk.key_column, lk.value_column, lk.header_row)
            if sig not in lookups:
                lk_sheet = find_sheet(names, lk.sheet)
                if lk_sheet is None:
                    raise ProfileMismatch(f"Lookup sheet '{lk.sheet}' not found")
                table_, dups = build_lookup(
                    data, lk_sheet, lk.key_column, lk.value_column, cfg, lk.header_row
                )
                lookups[sig] = (table_, dups)
                if dups:
                    shown = ", ".join(sorted(map(str, dups))[:5])
                    result.errors.append(
                        _issue(
                            "warning",
                            f"Lookup sheet '{lk.sheet}' repeats these codes "
                            f"(first match used): {shown}",
                        )
                    )
        used = used_headers(defn)
        for h in headers.labels():
            if norm_header(h) not in used:
                result.errors.append(
                    _issue(
                        "warning",
                        "Column is not mapped by the profile; its values are ignored",
                        column=h,
                    )
                )
        _read_sheet(data, sheet, tables, col_index, lookups, defn, cfg, result)
    return result


def _resolve_columns(sheet: str, headers: Headers, tables: list[TableRule]) -> dict[str, int]:
    index: dict[str, int] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for t in tables:
        for c in t.columns:
            i = headers.find(c.source)
            if i is None:
                if c.source not in missing:
                    missing.append(c.source)
            elif norm_header(c.source) in headers.duplicates:
                if c.source not in ambiguous:
                    ambiguous.append(c.source)
            else:
                index[c.source] = i
    if missing:
        raise ProfileMismatch(
            f"Sheet '{sheet}' has no column(s): {', '.join(missing)}",
            details=[{"column": m} for m in missing],
        )
    if ambiguous:
        raise ProfileMismatch(
            f"Sheet '{sheet}' has several columns named: {', '.join(ambiguous)}",
            details=[{"column": a} for a in ambiguous],
        )
    return index


def _read_sheet(data, sheet, tables, col_index, lookups, defn, cfg, result: ReadResult) -> None:
    seen: dict[str, dict[Any, int]] = {t.table: {} for t in tables}
    slots: dict[str, dict[Any, dict[str, Any]]] = {t.table: {} for t in tables}
    blank_streak = 0
    mapped = sorted({i for i in col_index.values()})

    for n, row, uncached in iter_sheet_rows(
        data, sheet, defn.data_start_row, cfg, check_formulas=cfg.check_formulas
    ):

        def cell(i: int, _row=row):
            return _row[i] if i < len(_row) else None

        if all(cell(i) is None or str(cell(i)).strip() == "" for i in mapped) and not (
            uncached & set(mapped)
        ):
            blank_streak += 1
            if blank_streak >= defn.stop_on_blank_rows:
                break
            continue
        blank_streak = 0
        result.rows_total += 1

        row_errors: list[dict[str, Any]] = []
        row_records: dict[str, dict[str, Any]] = {}
        for t in tables:
            rec: dict[str, Any] = {}
            for c in t.columns:
                i = col_index[c.source]
                raw = cell(i)
                try:
                    if i in uncached:
                        raise CoerceError(
                            "The cell is a formula without a stored result; open the file in "
                            "Excel, save it, and upload it again"
                        )
                    if c.lookup is not None:
                        raw = _apply_lookup(raw, c, lookups)
                    value = coerce(raw, c.type, c.format)
                    if value is None and c.default is not None:
                        value = coerce(c.default, c.type, c.format)
                    value = apply_operations(value, c.operations, c.type)
                    if value is None and c.required:
                        raise CoerceError("A value is required here")
                except CoerceError as exc:
                    row_errors.append(_issue("error", str(exc), n, c.source, raw))
                    value = None
                rec[c.target] = value
            for k in t.key:
                if rec[k] is None and not any(e["column"] == _source_of(t, k) for e in row_errors):
                    row_errors.append(
                        _issue("error", f"Key column '{k}' is empty", n, _source_of(t, k))
                    )
            row_records[t.table] = rec

        if row_errors:
            result.rows_failed += 1
            result.errors.extend(row_errors)
            continue
        result.rows_ok += 1
        for t in tables:
            rec = row_records[t.table]
            ident = tuple(rec[k] for k in t.key) if t.key else _row_hash(rec)
            if ident in seen[t.table]:
                earlier = seen[t.table][ident]
                if t.key:
                    msg = (
                        f"Table '{t.table}': key {list(ident)} also at row {earlier}; "
                        "the later row wins"
                    )
                else:
                    msg = f"Table '{t.table}': identical to row {earlier}; kept once"
                result.errors.append(_issue("warning", msg, n))
            seen[t.table][ident] = n
            slots[t.table][ident] = rec
    for t in tables:
        result.records[t.table] = list(slots[t.table].values())


def _source_of(t: TableRule, target: str) -> str | None:
    return next((c.source for c in t.columns if c.target == target), None)


def _apply_lookup(raw, c, lookups):
    lk = c.lookup
    table, _dups = lookups[(lk.sheet, lk.key_column, lk.value_column, lk.header_row)]
    key = lookup_key(raw)
    if key is None:
        return None
    if key not in table:
        raise CoerceError(f"Code '{raw}' was not found in sheet '{lk.sheet}' ({lk.key_column})")
    return table[key]


# ---------------------------------------------------------------------------
# Write phase
# ---------------------------------------------------------------------------
def write_records(
    session: Session, engine, defn: ProfileDefinition, result: ReadResult, upload_id, cfg
) -> dict[str, Any]:
    if engine.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": WRITE_LOCK_KEY})
    stamp = now()
    summary: dict[str, Any] = {}
    for t in defn.tables:
        specs = [ColSpec(c.target, c.type, c.label or c.source) for c in t.columns]
        table = ensure_table(session, engine, t.table, t.label or t.table, list(t.key), specs)
        targets = [c.target for c in t.columns]
        rows = [
            {**rec, "_row_hash": _row_hash(rec), "_source_upload": upload_id, "_updated_at": stamp}
            for rec in result.records.get(t.table, [])
        ]
        before = count_rows(session, table)
        upsert(session, engine, table, rows, list(t.key), targets, cfg.batch_size)
        after = count_rows(session, table)
        inserted = after - before
        summary[t.table] = {
            "rows": len(rows),
            "inserted": inserted,
            "updated_or_unchanged": len(rows) - inserted,
        }
    return summary


# ---------------------------------------------------------------------------
# Orchestration (runs in a background thread; owns its own DB session)
# ---------------------------------------------------------------------------
def run_import(
    factory: sessionmaker, engine, storage, cfg: IngestionConfig, run_id, redis=None
) -> None:
    with factory() as s:
        run = s.get(ImportRun, run_id)
        upload = s.get(Upload, run.upload_id)
        run.status, run.started_at = "running", utcnow()
        if not run.dry_run:
            upload.status = "importing"
        s.commit()
        actor = str(run.created_by) if run.created_by else None
        _progress(redis, run, actor)

        try:
            data = storage.get(upload.storage_key)
            defn = parse_definition(run.profile_snapshot)
            result = read_workbook(data, defn, cfg)
            if defn.strict and result.error_count:
                raise ImportAborted(
                    f"{result.rows_failed} row(s) have errors and this profile is strict; "
                    "nothing was imported"
                )
            if run.dry_run:
                tables = {k: {"rows": len(v), "dry_run": True} for k, v in result.records.items()}
            else:
                tables = write_records(s, engine, defn, result, upload.id, cfg)
            ordered = sorted(result.errors, key=lambda e: e["level"] != "error")
            run.errors = ordered[: cfg.max_stored_errors]
            run.errors_truncated = max(0, len(ordered) - cfg.max_stored_errors)
            run.rows_total, run.rows_ok, run.rows_failed = (
                result.rows_total,
                result.rows_ok,
                result.rows_failed,
            )
            run.tables = tables
            run.status = "done"
            if not run.dry_run:
                upload.status, upload.profile_id = "done", run.profile_id
            run.finished_at = utcnow()
            s.commit()
        except Exception as exc:
            s.rollback()
            msg = exc.message if isinstance(exc, AppError) else "Unexpected error during import"
            if not isinstance(exc, AppError):
                log.exception("import %s failed", run_id)
            run = s.get(ImportRun, run_id)
            upload = s.get(Upload, run.upload_id)
            run.status, run.error_message, run.finished_at = "failed", msg, utcnow()
            if isinstance(exc, AppError) and exc.details:
                run.errors = [
                    _issue("error", d.get("message") or json.dumps(d), column=d.get("column"))
                    for d in exc.details
                ][: cfg.max_stored_errors]
            if not run.dry_run:
                upload.status = "failed"
            s.commit()

        _progress(redis, run, actor)
        if run.status == "done" and not run.dry_run and redis is not None:
            for table, info in (run.tables or {}).items():
                _publish(
                    redis,
                    "data.imported",
                    {
                        "upload_id": str(run.upload_id),
                        "run_id": str(run.id),
                        "table": table,
                        "row_count": info["rows"],
                    },
                    actor,
                )


def _progress(redis, run: ImportRun, actor) -> None:
    if redis is None:
        return
    _publish(
        redis,
        "job.progress",
        {
            "kind": "import",
            "run_id": str(run.id),
            "status": run.status,
            "rows_total": run.rows_total,
            "rows_ok": run.rows_ok,
            "rows_failed": run.rows_failed,
        },
        actor,
    )


def _publish(redis, type_: str, data: dict, actor) -> None:
    try:
        events.publish(redis, type_, data, actor)
    except Exception:  # events are best-effort; the import itself already succeeded
        log.warning("could not publish %s", type_, exc_info=True)
