from unittest.mock import patch

import requests
from app.models import AuditLog
from sqlalchemy import select
from sqlalchemy.orm import Session


class FakeUpstream:
    def __init__(self, status=200, body=b'{"ok": true}', headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {
            "Content-Type": "application/json",
            "Set-Cookie": "x=1",
        }

    def iter_content(self, chunk_size=0):
        yield self._body


def test_requires_authentication(client):
    assert client.get("/api/blocks").status_code == 401


def test_unknown_segment_is_404(client, auth):
    assert client.get("/api/whatever/x", headers=auth()).status_code == 404


def test_role_enforced_before_forwarding(client, auth):
    with patch("app.api.proxy.requests.request") as fwd:
        r = client.post("/api/blocks", json={}, headers=auth("operator"))
    assert r.status_code == 403 and not fwd.called


def test_internal_routes_are_not_exposed(client, auth):
    with patch("app.api.proxy.requests.request") as fwd:
        r = client.patch("/api/ingestion/tables/s/rows/1", json={}, headers=auth("admin"))
    assert r.status_code == 404 and not fwd.called


def test_forwards_with_identity_and_service_token_and_strips_spoofed_headers(client, auth):
    h = {
        **auth("designer"),
        "X-User-Role": "admin",
        "X-Service-Token": "evil",
        "If-Match": "3",
    }
    with patch("app.api.proxy.requests.request", return_value=FakeUpstream()) as fwd:
        r = client.put("/api/blocks/b1?x=1&y=2", json={"a": 1}, headers=h)
    assert r.status_code == 200 and r.json == {"ok": True}
    args, kwargs = fwd.call_args
    assert args == ("PUT", "http://block:8000/blocks/b1?x=1&y=2")
    sent = kwargs["headers"]
    assert sent["X-Service-Token"] == "svc-token"
    assert sent["X-User-Role"] == "designer" and sent["X-User-Id"]
    assert sent["If-Match"] == "3"
    assert b'"a"' in kwargs["data"]
    assert "Set-Cookie" not in r.headers  # upstream cookies are dropped


def test_upstream_status_and_body_pass_through(client, auth):
    up = FakeUpstream(409, b'{"error": {"code": "CONFLICT"}}')
    with patch("app.api.proxy.requests.request", return_value=up):
        r = client.get("/api/reports/1", headers=auth("viewer"))
    assert r.status_code == 409 and r.json["error"]["code"] == "CONFLICT"


def test_unreachable_and_timeout_map_to_502_504(client, auth):
    h = auth("viewer")
    with patch("app.api.proxy.requests.request", side_effect=requests.ConnectionError()):
        assert client.get("/api/reports", headers=h).status_code == 502
    with patch("app.api.proxy.requests.request", side_effect=requests.Timeout()):
        assert client.get("/api/reports", headers=h).status_code == 504


def test_unconfigured_service_is_502(client, auth):
    r = client.get("/api/templates", headers=auth("viewer"))  # no template URL in test settings
    assert r.status_code == 502 and r.json["error"]["code"] == "UPSTREAM_ERROR"


def test_successful_posts_are_audited_but_puts_are_not(app, client, auth):
    h = auth("operator")
    with patch("app.api.proxy.requests.request", return_value=FakeUpstream(201)):
        client.post("/api/reports", json={}, headers=h)
        client.put("/api/reports/1", json={}, headers=h)
    with Session(app.extensions["db_engine"]) as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action.like("http.%"))).all()
    assert [r.action for r in rows] == ["http.post"]
    assert rows[0].meta == {"path": "/reports", "status": 201}
