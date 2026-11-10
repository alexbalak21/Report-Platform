import logging
import os

from flask import Flask
from rp_shared.app_factory import create_service_app
from rp_shared.config import Settings
from rp_shared.migrate import upgrade_to_head
from werkzeug.middleware.proxy_fix import ProxyFix

from .api import auth as auth_api
from .api import users as users_api
from .auth_service import bootstrap_admin
from .config import AuthConfig
from .db import init_db
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")
MIGRATION_LOCK_KEY = 7_201_001  # arbitrary, unique per service


def create_app(
    settings: Settings | None = None,
    config: AuthConfig | None = None,
    run_migrations: bool = False,
) -> Flask:
    """The auth service: users, login / refresh / logout, JWT issuing, audit log.

    Any module can use it: browsers reach it through the gateway (/api/auth, /api/users),
    other services can verify the access tokens it issues with the shared JWT secret.
    """
    app = create_service_app("auth", settings)
    cfg = config or AuthConfig.from_env()
    if len(app.config["SETTINGS"].jwt_secret.encode()) < 32:
        log.warning("JWT_SECRET is shorter than 32 bytes: set a long random value outside dev")
    app.config["AUTH"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    if cfg.trusted_proxies:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=cfg.trusted_proxies, x_proto=cfg.trusted_proxies
        )

    init_db(app)
    app.extensions["limiter"] = RateLimiter()
    for bp in (auth_api.bp, users_api.bp, users_api.audit_bp):
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
