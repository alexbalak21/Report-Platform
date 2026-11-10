"""Per-request SQLAlchemy session bound to the app's engine."""

from __future__ import annotations

from flask import Flask, current_app, g
from sqlalchemy.orm import Session, sessionmaker


def init_db(app: Flask) -> None:
    engine = app.extensions.get("db_engine")
    app.extensions["db_sessionmaker"] = (
        sessionmaker(engine, expire_on_commit=False) if engine is not None else None
    )

    @app.teardown_appcontext
    def _close(_exc):
        session = g.pop("db", None)
        if session is not None:
            session.close()


def get_session() -> Session:
    if "db" not in g:
        factory = current_app.extensions.get("db_sessionmaker")
        if factory is None:
            raise RuntimeError("INGESTION_DATABASE_URL is not configured")
        g.db = factory()
    return g.db
