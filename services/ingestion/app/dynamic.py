"""Business tables in the `data` schema, created and evolved from import profiles.

Safety rules: identifiers are validated by the profile schema (^[a-z][a-z0-9_]{0,62}$) and always
quoted; columns are only ever added (never dropped); a type change is refused unless it is a
pure widening (integer -> number). Every table carries system columns prefixed with `_`.
"""

from __future__ import annotations

import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime

from rp_shared.errors import Conflict
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Uuid,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import DataColumn, DataTable, utcnow

# SQLite has no Decimal type; tests on SQLite store floats. Postgres (production) is exact.
warnings.filterwarnings("ignore", message=".*does \\*not\\* support Decimal objects natively.*")

SA_TYPES = {
    "string": Text(),
    "number": Numeric(),
    "integer": BigInteger(),
    "date": Date(),
    "datetime": DateTime(),
    "boolean": Boolean(),
}
SYSTEM_COLUMNS = (
    "_id",
    "_source_upload",
    "_row_hash",
    "_created_at",
    "_updated_at",
    "_report_number",
)
WRITE_LOCK_KEY = 7_201_002  # serialises imports (DDL + upserts) on Postgres


@dataclass
class ColSpec:
    name: str
    type: str
    label: str


def data_schema(engine: Engine) -> str | None:
    return "data" if engine.dialect.name == "postgresql" else None


def build_table(engine: Engine, name: str, columns: list[tuple[str, str]], key: list[str]) -> Table:
    md = MetaData(schema=data_schema(engine))
    table = Table(
        name,
        md,
        Column("_id", Uuid, primary_key=True, default=uuid.uuid4),
        Column("_source_upload", Uuid, nullable=True),
        Column("_row_hash", String(64), nullable=False),
        Column("_created_at", DateTime(timezone=True), default=utcnow, nullable=False),
        Column("_updated_at", DateTime(timezone=True), default=utcnow, nullable=False),
        Column("_report_number", String(40), nullable=True),
        *[Column(n, SA_TYPES[t]) for n, t in columns],
    )
    if key:
        Index(f"uq_{name[:55]}_key", *[table.c[k] for k in key], unique=True)
    else:
        Index(f"uq_{name[:54]}_hash", table.c._row_hash, unique=True)
    return table


def _qualified(engine: Engine, name: str) -> str:
    pre = engine.dialect.identifier_preparer
    schema = data_schema(engine)
    return f"{pre.quote_schema(schema)}.{pre.quote(name)}" if schema else pre.quote(name)


def load_table(session: Session, engine: Engine, name: str) -> tuple[DataTable, Table] | None:
    dt = session.scalar(select(DataTable).where(DataTable.name == name))
    if dt is None:
        return None
    cols = session.scalars(
        select(DataColumn).where(DataColumn.table_id == dt.id).order_by(DataColumn.position)
    ).all()
    return dt, build_table(engine, name, [(c.name, c.type) for c in cols], list(dt.key_columns))


def ensure_table(
    session: Session,
    engine: Engine,
    name: str,
    label: str,
    key: list[str],
    columns: list[ColSpec],
) -> Table:
    """Create the table on first import; afterwards add new columns, refuse anything else."""
    conn = session.connection()
    pre = engine.dialect.identifier_preparer
    dt = session.scalar(select(DataTable).where(DataTable.name == name))

    if dt is None:
        if inspect(conn).has_table(name, schema=data_schema(engine)):
            raise Conflict(
                f"A table named '{name}' already exists but is not managed by an import profile",
                code="SCHEMA_CONFLICT",
            )
        dt = DataTable(name=name, label=label, key_columns=key)
        session.add(dt)
        session.flush()
        for i, c in enumerate(columns):
            session.add(
                DataColumn(table_id=dt.id, name=c.name, label=c.label, type=c.type, position=i)
            )
        session.flush()
        table = build_table(engine, name, [(c.name, c.type) for c in columns], key)
        table.metadata.create_all(conn)
        return table

    if list(dt.key_columns) != list(key):
        raise Conflict(
            f"Table '{name}' is keyed by {list(dt.key_columns) or 'row content'} but the profile "
            f"says {key or 'row content'}. Changing a table's key is not allowed.",
            code="SCHEMA_CONFLICT",
        )
    existing = {
        c.name: c
        for c in session.scalars(select(DataColumn).where(DataColumn.table_id == dt.id)).all()
    }
    position = max((c.position for c in existing.values()), default=-1) + 1
    for c in columns:
        ex = existing.get(c.name)
        if ex is None:
            ddl = SA_TYPES[c.type].compile(dialect=engine.dialect)
            conn.execute(
                text(f"ALTER TABLE {_qualified(engine, name)} ADD COLUMN {pre.quote(c.name)} {ddl}")
            )
            session.add(
                DataColumn(
                    table_id=dt.id, name=c.name, label=c.label, type=c.type, position=position
                )
            )
            position += 1
        elif ex.type != c.type:
            if (ex.type, c.type) == ("integer", "number"):  # widening
                if engine.dialect.name == "postgresql":
                    conn.execute(
                        text(
                            f"ALTER TABLE {_qualified(engine, name)} "
                            f"ALTER COLUMN {pre.quote(c.name)} TYPE NUMERIC"
                        )
                    )
                ex.type = "number"
            elif (ex.type, c.type) == ("number", "integer"):
                pass  # whole numbers fit in the existing numeric column
            else:
                raise Conflict(
                    f"Column '{name}.{c.name}' is {ex.type}; the profile says {c.type}. "
                    "Only integer -> number widening is allowed.",
                    code="SCHEMA_CONFLICT",
                )
    session.flush()
    return load_table(session, engine, name)[1]  # type: ignore[index]


def count_rows(session: Session, table: Table) -> int:
    return session.scalar(select(func.count()).select_from(table)) or 0


def upsert(
    session: Session,
    engine: Engine,
    table: Table,
    records: list[dict],
    key: list[str],
    columns: list[str],
    batch_size: int,
) -> None:
    """INSERT .. ON CONFLICT: re-importing a file updates rows by key (or skips identical rows)."""
    if not records:
        return
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(table)
    if key:
        update = [*columns, "_row_hash", "_source_upload", "_updated_at"]
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c[k] for k in key],
            set_={c: stmt.excluded[c] for c in update},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[table.c._row_hash])
    conn = session.connection()
    for i in range(0, len(records), batch_size):
        conn.execute(stmt, records[i : i + batch_size])


def now() -> datetime:
    return utcnow()
