"""Run a service's Alembic migrations programmatically (used at service start-up).

Each service owns its own `migrations/` directory and its own version table (the first
schema of its DB role's search_path), so services never touch each other's history.
A Postgres advisory lock makes concurrent starts safe: one process migrates, the rest wait.
"""

from __future__ import annotations

import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


def upgrade_to_head(engine: Engine, script_location: str, lock_key: int) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", script_location)
    with engine.connect() as conn:
        is_pg = conn.dialect.name == "postgresql"
        if is_pg:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": lock_key})
        try:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
        finally:
            if is_pg:
                conn.rollback()
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                conn.commit()
    log.info("migrations up to date (%s)", script_location)
