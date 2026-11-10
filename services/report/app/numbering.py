"""Atomic report numbers: `yymmdd-N`, N counting the reports of that day.

One upsert statement both creates the day's counter and increments it, then returns the new
value, so two users generating at the same moment can never get the same number (the desktop
app counted Excel rows, which raced). It runs inside the caller's transaction: if the report
fails to render after the number was taken, the rollback gives the number back (no gaps).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from .models import ReportCounter


def format_number(day: date, n: int) -> str:
    return f"{day:%y%m%d}-{n}"


def next_number(session: Session, day: date) -> str:
    dialect = session.get_bind().dialect.name
    insert = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}[dialect]
    stmt = insert(ReportCounter).values(day=day, last_n=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ReportCounter.day],
        set_={"last_n": ReportCounter.last_n + 1},
    ).returning(ReportCounter.last_n)
    n = session.execute(stmt).scalar_one()
    return format_number(day, n)
