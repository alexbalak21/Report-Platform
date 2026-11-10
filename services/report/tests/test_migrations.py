import os

import pytest
from alembic import command
from alembic.config import Config
from app import MIGRATION_LOCK_KEY, MIGRATIONS_DIR
from app.models import Base
from rp_shared.migrate import upgrade_to_head
from sqlalchemy import create_engine, inspect


def test_migration_matches_models_and_is_reversible_on_sqlite(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
    upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)  # idempotent
    insp = inspect(engine)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}
    uniques = {u["name"] for u in insp.get_unique_constraints("reports")}
    assert {"uq_reports_number", "uq_reports_record"} <= uniques

    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
        conn.commit()
    assert inspect(engine).get_table_names() == ["alembic_version"]


@pytest.mark.skipif(not os.getenv("TEST_PG_URL"), reason="set TEST_PG_URL to test on Postgres")
def test_migration_on_postgres_uses_jsonb():
    from sqlalchemy import text

    url = os.environ["TEST_PG_URL"]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP SCHEMA IF EXISTS reports CASCADE"))
        c.execute(text("CREATE SCHEMA reports"))
    engine = create_engine(f"{url}?options=-csearch_path%3Dreports")
    upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
    cols = {c["name"]: str(c["type"]) for c in inspect(engine).get_columns("reports")}
    assert cols["unresolved_keys"] == "JSONB"
    engine.dispose()
