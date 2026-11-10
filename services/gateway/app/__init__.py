import logging
import os

from flask import Flask
from rp_shared.app_factory import create_service_app
from rp_shared.config import Settings
from rp_shared.migrate import upgrade_to_head
from werkzeug.middleware.proxy_fix import ProxyFix

from .api import auth as auth_api
from .api import events as events_api
from .api import proxy as proxy_api
from .api import users as users_api
from .api.health import bp as health_bp
from .auth_service import bootstrap_admin
from .config import GatewayConfig
from .db import init_db
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")
MIGRATION_LOCK_KEY = 7_201_001  # arbitrary, unique per service


def create_app(
    settings: Settings | None = None,
    config: GatewayConfig | None = None,
    run_migrations: bool = False,
) -> Flask:
    app = create_service_app("gateway", settings)
    cfg = config or GatewayConfig.from_env()
    if len(app.config["SETTINGS"].jwt_secret.encode()) < 32:
        log.warning("JWT_SECRET is shorter than 32 bytes: set a long random value outside dev")
    app.config["GATEWAY"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_upload_bytes
    if cfg.trusted_proxies:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=cfg.trusted_proxies, x_proto=cfg.trusted_proxies
        )

    init_db(app)
    app.extensions["limiter"] = RateLimiter()

    # Registration order doesn't matter: Flask prefers the static /api/auth/... rules over
    # the proxy's /api/<segment>/<path> catch-all.
    for bp in (
        health_bp,
        auth_api.bp,
        users_api.bp,
        users_api.audit_bp,
        events_api.bp,
        proxy_api.bp,
    ):
        app.register_blueprint(bp)

    engine = app.extensions.get("db_engine")
    if run_migrations and engine is not None:
        upgrade_to_head(engine, MIGRATIONS_DIR, MIGRATION_LOCK_KEY)
        with app.extensions["db_sessionmaker"]() as session:
            if bootstrap_admin(session, cfg.admin_email, cfg.admin_password):
                log.info("created bootstrap admin %s", cfg.admin_email)
            elif cfg.admin_email is None:
                log.info("no ADMIN_EMAIL set; skipping bootstrap admin")
    return app
