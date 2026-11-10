"""Per-request SQLAlchemy session bound to the app's engine."""

from __future__ import annotations

from flask import Flask, g
from sqlalchemy.orm import sessionmaker


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
