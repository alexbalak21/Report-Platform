import logging
import os

from flask import Flask
from rp_shared.app_factory import create_service_app, use_iso_json
from rp_shared.config import Settings
from rp_shared.migrate import upgrade_to_head

from .api import templates as templates_api
from .clients import BlockDirectory, CatalogClient, DataSource
from .consumer import start_consumer
from .db import init_db

log = logging.getLogger(__name__)
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")
MIGRATION_LOCK_KEY = 7_201_005  # arbitrary, unique per service


def create_app(
    settings: Settings | None = None,
    run_migrations: bool = False,
    consume_events: bool = False,
    blocks=None,
    catalog=None,
    data=None,
) -> Flask:
    """`blocks`, `catalog` and `data` default to HTTP clients of the other services; tests
    pass fakes."""
    app = create_service_app("template", settings)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    use_iso_json(app)
    init_db(app)
    cfg = app.config["SETTINGS"]
    app.extensions["blocks"] = blocks or BlockDirectory(cfg)
    app.extensions["catalog"] = catalog or CatalogClient(cfg)
    app.extensions["data"] = data or DataSource(cfg)
    app.register_blueprint(templates_api.bp)

    engine = app.extensions.get("db_engine")
    if run_migrations and engine is not None:
        upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
    if consume_events:
        start_consumer(app)
    return app
