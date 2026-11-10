import json

import pytest
from flask import g, jsonify

from rp_shared.app_factory import create_service_app
from rp_shared.auth import (
    decode_access_token,
    hash_password,
    make_access_token,
    require_role,
    require_service_token,
    verify_password,
)
from rp_shared.config import Settings
from rp_shared.errors import NotFound, Unauthorized
from rp_shared.events import STREAM, build_event, consume_once, ensure_group, publish
from rp_shared.sanitizer import sanitize_html


@pytest.fixture()
def app():
    app = create_service_app("test", Settings(service_name="test", service_token="tok"))

    @app.get("/boom")
    def boom():
        raise RuntimeError("x")

    @app.get("/missing")
    def missing():
        raise NotFound("nothing here")

    @app.get("/internal")
    @require_service_token
    @require_role("designer")
    def internal():
        return jsonify(user=g.user_id)

    return app


def test_health_and_ready(app):
    c = app.test_client()
    assert c.get("/healthz").json["status"] == "ok"
    assert c.get("/readyz").status_code == 200  # no db/redis configured -> nothing to fail


def test_error_envelope_and_correlation_id(app):
    c = app.test_client()
    r = c.get("/missing", headers={"X-Correlation-Id": "abc"})
    assert r.status_code == 404
    assert r.json["error"]["code"] == "NOT_FOUND"
    assert r.json["error"]["correlation_id"] == "abc"
    assert r.headers["X-Correlation-Id"] == "abc"
    assert c.get("/boom").json["error"]["code"] == "INTERNAL_ERROR"
    assert c.get("/nope").status_code == 404


def test_service_token_and_roles(app):
    c = app.test_client()
    assert c.get("/internal").status_code == 401
    h = {"X-Service-Token": "tok", "X-User-Id": "u1", "X-User-Role": "operator"}
    assert c.get("/internal", headers=h).status_code == 403
    h["X-User-Role"] = "designer"
    assert c.get("/internal", headers=h).json == {"user": "u1"}
    h["X-User-Role"] = "admin"  # admin always passes
    assert c.get("/internal", headers=h).status_code == 200


def test_jwt_roundtrip_and_password_hash():
    secret = "a-test-secret-that-is-long-enough-for-hs256"
    tok = make_access_token(secret, "u1", "viewer")
    assert decode_access_token(secret, tok)["role"] == "viewer"
    with pytest.raises(Unauthorized):
        decode_access_token("another-test-secret-that-is-long-enough!!", tok)
    h = hash_password("pw")
    assert verify_password(h, "pw") and not verify_password(h, "nope")


def test_sanitizer_strips_scripts_and_handlers():
    dirty = '<p onclick="x()">hi<script>alert(1)</script><img src=x onerror=y></p>'
    clean = sanitize_html(dirty)
    assert "script" not in clean and "onclick" not in clean and "onerror" not in clean
    assert "<p>" in clean


def test_sanitizer_keeps_data_locked_attr_and_table():
    out = sanitize_html('<section data-locked="true"><table><tr><td>1</td></tr></table></section>')
    assert 'data-locked="true"' in out and "<table>" in out


def test_event_envelope():
    e = build_event("template.published", {"id": "t1"}, actor="u1", correlation_id="c1")
    assert e["type"] == "template.published" and e["correlation_id"] == "c1"


def test_publish_and_consume_with_fakeredis():
    fakeredis = pytest.importorskip("fakeredis")
    r = fakeredis.FakeRedis(decode_responses=True)
    ensure_group(r, "report")
    publish(r, "template.published", {"template_id": "t1"})
    publish(r, "unrelated.event", {})
    seen = []
    n = consume_once(r, "report", "c1", {"template.published": seen.append}, block_ms=10)
    assert n == 1 and seen[0]["data"] == {"template_id": "t1"}
    assert r.xpending(STREAM, "report")["pending"] == 0  # both acked


def test_failing_handler_stays_pending():
    fakeredis = pytest.importorskip("fakeredis")
    r = fakeredis.FakeRedis(decode_responses=True)
    ensure_group(r, "g")
    publish(r, "x", {})

    def bad(_e):
        raise RuntimeError("nope")

    assert consume_once(r, "g", "c", {"x": bad}, block_ms=10) == 0
    assert r.xpending(STREAM, "g")["pending"] == 1
    json.dumps({})  # keep import used


def test_require_min_role_hierarchy():
    from flask import Flask, g

    from rp_shared.auth import require_min_role
    from rp_shared.errors import Forbidden, Unauthorized

    app = Flask("t")

    @require_min_role("operator")
    def action():
        return "ok"

    with app.test_request_context():
        with pytest.raises(Unauthorized):
            action()
        cases = [("viewer", False), ("operator", True), ("designer", True), ("admin", True)]
        for role, allowed in cases:
            g.user_role = role
            if allowed:
                assert action() == "ok"
            else:
                with pytest.raises(Forbidden):
                    action()


def test_iso_json_provider():
    from datetime import date, datetime
    from decimal import Decimal

    from flask import Flask

    from rp_shared.app_factory import use_iso_json

    app = Flask("t")
    use_iso_json(app)
    with app.app_context():
        payload = {"d": date(2026, 6, 21), "t": datetime(2026, 6, 21, 8, 5), "n": Decimal("12.50")}
        out = app.json.dumps(payload)
    assert json.loads(out) == {"d": "2026-06-21", "t": "2026-06-21T08:05:00", "n": "12.50"}
