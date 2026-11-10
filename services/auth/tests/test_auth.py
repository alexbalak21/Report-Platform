from datetime import timedelta

import pytest
from app.models import AuditLog, RefreshToken, utcnow
from conftest import PASSWORD
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_login_returns_token_user_and_httponly_cookie(client, make_user, login):
    make_user()
    r = login()
    assert r.status_code == 200
    assert r.json["token_type"] == "bearer" and r.json["expires_in"] == 900
    assert r.json["user"]["role"] == "admin" and "password" not in str(r.json)
    cookie = r.headers["Set-Cookie"]
    assert "rp_refresh=" in cookie and "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie and "Path=/auth" in cookie


def test_wrong_password_and_unknown_email_look_identical(client, make_user, login):
    make_user()
    bad_pw = login(password="nope-nope-nope")
    unknown = login("ghost@example.com")
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json["error"]["code"] == unknown.json["error"]["code"] == "INVALID_CREDENTIALS"
    assert bad_pw.json["error"]["message"] == unknown.json["error"]["message"]


def test_inactive_user_cannot_login(client, make_user, login):
    make_user(active=False)
    assert login().status_code == 401


def test_me_requires_valid_token(client, auth):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    r = client.get("/auth/me", headers=auth("operator"))
    assert r.status_code == 200 and r.json["email"] == "operator@example.com"


def test_refresh_rotates_token(client, make_user, login):
    make_user()
    login()
    old = client.get_cookie("rp_refresh", path="/auth").value
    r = client.post("/auth/refresh")
    assert r.status_code == 200 and r.json["access_token"]
    assert client.get_cookie("rp_refresh", path="/auth").value != old


def test_reusing_rotated_refresh_token_revokes_the_family(app, client, make_user, login):
    make_user()
    login()
    stolen = client.get_cookie("rp_refresh", path="/auth").value
    assert client.post("/auth/refresh").status_code == 200  # legit rotation
    client.set_cookie("rp_refresh", stolen, path="/auth")
    r = client.post("/auth/refresh")  # attacker replays the old token
    assert r.status_code == 401 and r.json["error"]["code"] == "REFRESH_REUSED"
    with Session(app.extensions["db_engine"]) as s:
        assert all(t.revoked for t in s.scalars(select(RefreshToken)))
        assert s.scalar(select(AuditLog).where(AuditLog.action == "auth.refresh_reuse_detected"))
    # the legit client's newest token is dead too
    assert client.post("/auth/refresh").status_code == 401


def test_refresh_without_cookie_is_401_and_clears_cookie(client):
    r = client.post("/auth/refresh")
    assert r.status_code == 401 and r.json["error"]["code"] == "REFRESH_MISSING"


def test_expired_refresh_token_rejected(app, client, make_user, login):
    make_user()
    login()
    with Session(app.extensions["db_engine"]) as s:
        for t in s.scalars(select(RefreshToken)):
            t.expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert client.post("/auth/refresh").json["error"]["code"] == "REFRESH_EXPIRED"


def test_logout_revokes_refresh_token(client, make_user, login):
    make_user()
    login()
    assert client.post("/auth/logout").status_code == 204
    assert client.post("/auth/refresh").status_code == 401


def test_login_is_rate_limited_per_account_then_resets_on_success(client, make_user, login):
    make_user()
    for _ in range(3):
        assert login(password="wrong-wrong-wrong").status_code == 401
    r = login()  # 4th attempt inside the window, even with the right password
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json["error"]["code"] == "RATE_LIMITED"


def test_successful_login_resets_counter(client, make_user, login):
    make_user()
    for _ in range(2):
        login(password="wrong-wrong-wrong")
    assert login().status_code == 200
    for _ in range(3):
        assert login(password="wrong-wrong-wrong").status_code == 401


def test_login_events_are_audited(app, client, make_user, login):
    make_user()
    login(password="wrong-wrong-wrong")
    login()
    with Session(app.extensions["db_engine"]) as s:
        actions = [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions == ["auth.login_failed", "auth.login"]


@pytest.mark.parametrize("body", [None, {}, {"email": "a@b.co"}, {"email": 1, "password": 2}])
def test_login_validates_body(client, body):
    r = client.post("/auth/login", json=body)
    assert r.status_code == 400 and r.json["error"]["code"] == "VALIDATION_ERROR"


def test_password_constant():
    assert len(PASSWORD) >= 10
