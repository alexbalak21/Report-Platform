import logging
import os

from flask import Flask
from rp_shared.app_factory import create_service_app, use_iso_json
from rp_shared.config import Settings
from rp_shared.migrate import upgrade_to_head
from rp_shared.storage import make_storage

from .api import reports as reports_api
from .clients import BlockDirectory, DataSource, TemplateDirectory
from .config import ReportConfig
from .db import init_db
from .generate import Deps
from .jobs import JobRunner, recover_interrupted
from .pdf import PlaywrightPdfRenderer

log = logging.getLogger(__name__)
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")
MIGRATION_LOCK_KEY = 7_201_004  # arbitrary, unique per service


def create_app(
    settings: Settings | None = None,
    config: ReportConfig | None = None,
    run_migrations: bool = False,
    consume_events: bool = False,  # reserved: the editor's `report.revised` arrives in Phase 6
    storage=None,
    pdf=None,
    templates=None,
    blocks=None,
    data=None,
) -> Flask:
    """`storage`, `pdf`, `templates`, `blocks` and `data` default to the real thing (S3/disk,
    Chromium, HTTP clients of the other services); tests pass fakes."""
    app = create_service_app("report", settings)
    cfg = config or ReportConfig.from_env()
    svc = app.config["SETTINGS"]
    app.config["REPORT"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    use_iso_json(app)
    init_db(app)
    app.extensions["report_deps"] = Deps(
        templates=templates or TemplateDirectory(svc),
        blocks=blocks or BlockDirectory(svc),
        data=data or DataSource(svc),
        storage=storage or make_storage(cfg),
        config=cfg,
    )
    app.extensions["pdf"] = pdf or PlaywrightPdfRenderer(cfg.pdf_timeout_ms, cfg.pdf_allowed_hosts)
    app.extensions["jobs"] = JobRunner(sync=cfg.sync_jobs)
    app.register_blueprint(reports_api.bp)

    engine = app.extensions.get("db_engine")
    if run_migrations and engine is not None:
        upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
        n = recover_interrupted(app.extensions["db_sessionmaker"])
        if n:
            log.warning("closed %d interrupted batch job(s) as failed", n)
    return app
