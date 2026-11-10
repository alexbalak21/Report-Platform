import os

import pytest
from app import MIGRATION_LOCK_KEY, MIGRATIONS_DIR
from app.auth_service import bootstrap_admin
from app.models import Base
from rp_shared.migrate import upgrade_to_head
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session


def test_migration_matches_models_on_sqlite(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
    upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)  # idempotent
    insp = inspect(engine)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}


@pytest.mark.skipif(not os.getenv("TEST_PG_URL"), reason="set TEST_PG_URL to test on Postgres")
def test_migration_and_bootstrap_on_postgres():
    engine = create_engine(os.environ["TEST_PG_URL"])
    upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
    with Session(engine) as s:
        bootstrap_admin(s, "boot@example.com", "a-long-password")
        assert (
            bootstrap_admin(s, "other@example.com", "a-long-password") is False
        )  # only when empty
