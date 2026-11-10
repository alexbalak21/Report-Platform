from unittest.mock import patch

import requests


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


def test_successful_posts_are_audited_but_puts_are_not(client, auth):
    h = auth("operator")
    with (
        patch("app.api.proxy.requests.request", return_value=FakeUpstream(201)),
        patch("app.api.proxy.ServiceClient") as audit_client,
    ):
        client.post("/api/reports", json={}, headers=h)
        client.put("/api/reports/1", json={}, headers=h)
    calls = audit_client.return_value.request.call_args_list
    assert len(calls) == 1
    method, path = calls[0].args
    body = calls[0].kwargs["json"]
    assert (method, path) == ("POST", "/audit")
    assert body["action"] == "http.post" and body["resource_type"] == "report"
    assert body["meta"] == {"path": "/reports", "status": 201} and body["user_id"]


def test_a_failing_audit_never_fails_the_request(client, auth):
    with (
        patch("app.api.proxy.requests.request", return_value=FakeUpstream(201)),
        patch("app.api.proxy.ServiceClient") as audit_client,
    ):
        audit_client.return_value.request.side_effect = RuntimeError("auth is down")
        r = client.post("/api/reports", json={}, headers=auth("operator"))
    assert r.status_code == 201


def test_failed_calls_are_not_audited(client, auth):
    with (
        patch("app.api.proxy.requests.request", return_value=FakeUpstream(422)),
        patch("app.api.proxy.ServiceClient") as audit_client,
    ):
        client.post("/api/reports", json={}, headers=auth("operator"))
    audit_client.return_value.request.assert_not_called()


# --------------------------------------------------------------------------- auth passthrough
class FakeAuthUpstream(FakeUpstream):
    def __init__(self, status=200, cookies=("rp_refresh=abc; Path=/api/auth; HttpOnly",)):
        super().__init__(status, headers={"Content-Type": "application/json"})
        self.raw = type(
            "Raw", (), {"headers": type("H", (), {"getlist": lambda s, k: list(cookies)})()}
        )()


def test_login_needs_no_token_and_goes_to_the_auth_service(client):
    client.set_cookie("rp_refresh", "1")
    with patch("app.api.proxy.requests.request", return_value=FakeAuthUpstream()) as req:
        r = client.post(
            "/api/auth/login",
            json={"email": "a@b.c", "password": "x"},
            headers={"X-User-Role": "admin", "X-Service-Token": "forged"},
            environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
        )
    assert r.status_code == 200
    method, url = req.call_args.args
    assert (method, url) == ("POST", "http://auth:8000/auth/login")
    sent = req.call_args.kwargs["headers"]
    assert "rp_refresh=1" in sent["Cookie"] and sent["X-Forwarded-For"] == "203.0.113.7"
    # no identity, no service token: the auth service verifies the bearer token itself
    assert "X-User-Role" not in sent and "X-Service-Token" not in sent and "X-User-Id" not in sent


def test_bearer_token_and_set_cookie_pass_through_for_auth_routes(client, auth):
    h = auth("admin")
    with patch("app.api.proxy.requests.request", return_value=FakeAuthUpstream()) as req:
        r = client.get("/api/users?x=1", headers=h)
    assert req.call_args.args == ("GET", "http://auth:8000/users?x=1")
    assert req.call_args.kwargs["headers"]["Authorization"] == h["Authorization"]
    assert (
        "rp_refresh=abc" in r.headers["Set-Cookie"] and "Path=/api/auth" in r.headers["Set-Cookie"]
    )


def test_auth_routes_are_not_audited_by_the_gateway(client):
    with (
        patch("app.api.proxy.requests.request", return_value=FakeAuthUpstream()),
        patch("app.api.proxy.ServiceClient") as audit_client,
    ):
        client.post("/api/auth/login", json={})
    audit_client.return_value.request.assert_not_called()


def test_audit_writes_are_not_reachable_from_a_browser(client, auth):
    h = auth("admin")
    assert client.post("/api/audit", json={"action": "x"}, headers=h).status_code == 404
    with patch("app.api.proxy.requests.request", return_value=FakeAuthUpstream()):
        assert (
            client.get("/api/audit", headers=h).status_code == 200
        )  # reading is the auth service's call


def test_auth_service_down_maps_to_502(client):
    with patch("app.api.proxy.requests.request", side_effect=requests.ConnectionError()):
        r = client.post("/api/auth/login", json={})
    assert r.status_code == 502 and r.json["error"]["code"] == "UPSTREAM_ERROR"
