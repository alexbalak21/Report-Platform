def test_only_admin_can_manage_users(client, auth):
    assert client.get("/api/users", headers=auth("designer")).status_code == 403
    assert client.get("/api/users").status_code == 401
    r = client.get("/api/users", headers=auth("admin"))
    assert r.status_code == 200 and r.json["total"] >= 1


def test_create_user_and_login_as_them(client, auth, login):
    h = auth("admin")
    r = client.post(
        "/api/users",
        headers=h,
        json={
            "email": "  New.Person@Example.com ",
            "password": "a-long-password",
            "display_name": "New",
            "role": "viewer",
        },
    )
    assert r.status_code == 201 and r.json["email"] == "new.person@example.com"
    assert login("new.person@example.com", "a-long-password").status_code == 200


def test_create_user_rejects_bad_input(client, auth):
    h = auth("admin")
    base = {
        "email": "x@example.com",
        "password": "a-long-password",
        "display_name": "X",
    }
    assert (
        client.post("/api/users", headers=h, json={**base, "password": "short"}).status_code == 400
    )
    assert client.post("/api/users", headers=h, json={**base, "email": "nope"}).status_code == 400
    assert client.post("/api/users", headers=h, json={**base, "role": "root"}).status_code == 400
    assert client.post("/api/users", headers=h, json=base).status_code == 201
    dup = client.post("/api/users", headers=h, json=base)
    assert dup.status_code == 409 and dup.json["error"]["code"] == "EMAIL_TAKEN"


def test_cannot_remove_last_admin(client, auth, make_user):
    h = auth("admin")
    me = client.get("/api/auth/me", headers=h).json["id"]
    r = client.patch(f"/api/users/{me}", headers=h, json={"role": "viewer"})
    assert r.status_code == 409 and r.json["error"]["code"] == "LAST_ADMIN"
    assert client.patch(f"/api/users/{me}", headers=h, json={"is_active": False}).status_code == 409
    make_user("second@example.com", "admin")
    assert client.patch(f"/api/users/{me}", headers=h, json={"role": "viewer"}).status_code == 200


def test_deactivating_user_kills_their_sessions(client, auth, make_user, login):
    h = auth("admin")
    uid = make_user("victim@example.com", "operator")
    login("victim@example.com")
    assert (
        client.patch(f"/api/users/{uid}", headers=h, json={"is_active": False}).status_code == 200
    )
    assert client.post("/api/auth/refresh").status_code == 401


def test_password_reset_revokes_sessions_and_changes_password(client, auth, make_user, login):
    h = auth("admin")
    uid = make_user("u@example.com", "operator")
    login("u@example.com")
    r = client.patch(f"/api/users/{uid}", headers=h, json={"password": "brand-new-password"})
    assert r.status_code == 200
    assert client.post("/api/auth/refresh").status_code == 401
    assert login("u@example.com").status_code == 401
    assert login("u@example.com", "brand-new-password").status_code == 200


def test_unknown_user_is_404(client, auth):
    h = auth("admin")
    assert client.get("/api/users/not-a-uuid", headers=h).status_code == 404
    assert (
        client.patch(
            "/api/users/00000000-0000-0000-0000-000000000000", headers=h, json={}
        ).status_code
        == 404
    )


def test_audit_log_visible_to_admin_only(client, auth):
    assert client.get("/api/audit", headers=auth("operator")).status_code == 403
    r = client.get("/api/audit", headers=auth("admin"))
    assert r.status_code == 200 and any(i["action"] == "auth.login" for i in r.json["items"])
