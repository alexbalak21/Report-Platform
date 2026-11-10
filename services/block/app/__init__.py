import logging
import os

from flask import Flask
from rp_shared.app_factory import create_service_app, use_iso_json
from rp_shared.config import Settings
from rp_shared.migrate import upgrade_to_head

from .api import blocks as blocks_api
from .db import init_db
from .seed import seed_defaults

log = logging.getLogger(__name__)
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")
MIGRATION_LOCK_KEY = 7_201_004  # arbitrary, unique per service


def create_app(settings: Settings | None = None, run_migrations: bool = False) -> Flask:
    app = create_service_app("block", settings)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # previews carry sample rows
    use_iso_json(app)
    init_db(app)
    app.register_blueprint(blocks_api.bp)

    engine = app.extensions.get("db_engine")
    if run_migrations and engine is not None:
        upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
        seed_defaults(app.extensions["db_sessionmaker"])
    return app
