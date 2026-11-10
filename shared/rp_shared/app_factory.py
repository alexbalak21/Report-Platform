"""Flask app factory shared by every service: logging, errors, /healthz, /readyz."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, jsonify, request
from flask.json.provider import DefaultJSONProvider

from . import __version__
from .config import Settings
from .db import make_engine, ping
from .errors import register_error_handlers
from .logging import CORRELATION_HEADER, configure_logging, set_correlation_id

log = logging.getLogger(__name__)


class IsoJSONProvider(DefaultJSONProvider):
    """Dates as ISO-8601 (Flask's default is RFC 822) and Decimals as strings (no float loss)."""

    @staticmethod
    def default(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return format(o, "f")
        return DefaultJSONProvider.default(o)


def use_iso_json(app: Flask) -> None:
    app.json = IsoJSONProvider(app)


def create_service_app(name: str, settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env(name)
    configure_logging(name, settings.log_level)

    app = Flask(name)
    app.config["SETTINGS"] = settings
    app.config["JSON_SORT_KEYS"] = False

    db_url = settings.database_url
    app.extensions["db_engine"] = make_engine(db_url) if db_url else None
    app.extensions["redis"] = None
    if settings.redis_url:
        import redis

        app.extensions["redis"] = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    register_error_handlers(app)

    @app.before_request
    def _correlation():
        set_correlation_id(request.headers.get(CORRELATION_HEADER))

    @app.after_request
    def _echo_correlation(resp):
        from .logging import get_correlation_id

        cid = get_correlation_id()
        if cid:
            resp.headers[CORRELATION_HEADER] = cid
        return resp

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service=name, version=__version__)

    @app.get("/readyz")
    def readyz():
        checks: dict[str, str] = {}
        engine = app.extensions.get("db_engine")
        if engine is not None:
            try:
                ping(engine)
                checks["postgres"] = "ok"
            except Exception as exc:
                checks["postgres"] = f"error: {exc.__class__.__name__}"
        r = app.extensions.get("redis")
        if r is not None:
            try:
                r.ping()
                checks["redis"] = "ok"
            except Exception as exc:
                checks["redis"] = f"error: {exc.__class__.__name__}"
        ok = all(v == "ok" for v in checks.values())
        return jsonify(status="ready" if ok else "degraded", service=name, checks=checks), (
            200 if ok else 503
        )

    return app
