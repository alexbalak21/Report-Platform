import logging
import os

from flask import Flask
from rp_shared.app_factory import create_service_app, use_iso_json
from rp_shared.config import Settings
from rp_shared.migrate import upgrade_to_head

from .api import profiles as profiles_api
from .api import runs as runs_api
from .api import tables as tables_api
from .api import uploads as uploads_api
from .config import IngestionConfig
from .db import init_db
from .jobs import JobRunner, recover_interrupted
from .storage import make_storage

log = logging.getLogger(__name__)
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")
MIGRATION_LOCK_KEY = 7_201_003  # arbitrary, unique per service


def create_app(
    settings: Settings | None = None,
    config: IngestionConfig | None = None,
    storage=None,
    run_migrations: bool = False,
) -> Flask:
    app = create_service_app("ingestion", settings)
    cfg = config or IngestionConfig.from_env()
    app.config["INGESTION"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_upload_bytes + 1024 * 1024  # multipart overhead
    use_iso_json(app)
    init_db(app)
    app.extensions["storage"] = storage or make_storage(cfg)
    app.extensions["jobs"] = JobRunner(sync=cfg.sync_jobs)

    for bp in (uploads_api.bp, runs_api.bp, profiles_api.bp, tables_api.bp):
        app.register_blueprint(bp)

    engine = app.extensions.get("db_engine")
    if run_migrations and engine is not None:
        upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
        n = recover_interrupted(app.extensions["db_sessionmaker"])
        if n:
            log.warning("marked %d interrupted import(s) as failed", n)
    return app
