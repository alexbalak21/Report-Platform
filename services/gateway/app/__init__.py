import logging

from flask import Flask
from rp_shared.app_factory import create_service_app
from rp_shared.config import Settings
from werkzeug.middleware.proxy_fix import ProxyFix

from .api import events as events_api
from .api import proxy as proxy_api
from .api.health import bp as health_bp
from .config import GatewayConfig

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, config: GatewayConfig | None = None) -> Flask:
    """The single entry point: verifies the access token (issued by the auth service), applies
    the role policy, and forwards to the module services. It keeps no state of its own."""
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
    # Flask prefers the static /api/health and /api/events rules over the proxy catch-all.
    for bp in (health_bp, events_api.bp, proxy_api.bp):
        app.register_blueprint(bp)
    return app
