import uuid

from flask import Blueprint, current_app, g, jsonify, make_response, request
from pydantic import BaseModel
from rp_shared.auth import make_access_token
from rp_shared.errors import Unauthorized, error_body

from .. import auth_service as svc
from ..db import get_session
from ..models import User
from ..security import parse_body, require_auth

bp = Blueprint("auth", __name__, url_prefix="/auth")

COOKIE = "rp_refresh"


class LoginIn(BaseModel):
    email: str
    password: str


def _token_response(user: User, refresh_raw: str, status: int = 200):
    cfg = current_app.config["AUTH"]
    settings = current_app.config["SETTINGS"]
    access = make_access_token(
        settings.jwt_secret, str(user.id), user.role, minutes=cfg.access_token_minutes
    )
    resp = make_response(
        jsonify(
            access_token=access,
            token_type="bearer",
            expires_in=cfg.access_token_minutes * 60,
            user=svc.user_dict(user),
        ),
        status,
    )
    resp.set_cookie(
        COOKIE,
        refresh_raw,
        max_age=cfg.refresh_token_days * 86400,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="Strict",
        path=cfg.cookie_path,
    )
    return resp


def _clear_cookie(resp):
    resp.delete_cookie(COOKIE, path=current_app.config["AUTH"].cookie_path)
    return resp


@bp.post("/login")
def login():
    body = parse_body(LoginIn)
    cfg = current_app.config["AUTH"]
    redis = current_app.extensions.get("redis")
    limiter = current_app.extensions["limiter"]
    ip = request.remote_addr or "unknown"
    email = body.email.strip().lower()
    account_key = f"login:{ip}:{email}"
    limiter.hit(redis, f"login-ip:{ip}", cfg.login_limit_per_ip, cfg.login_window_seconds)
    limiter.hit(redis, account_key, cfg.login_limit_per_account, cfg.login_window_seconds)

    session = get_session()
    user = svc.authenticate(session, body.email, body.password, ip)
    limiter.reset(redis, account_key)
    refresh_raw = svc.login(session, user, cfg.refresh_token_days, ip)
    return _token_response(user, refresh_raw)


@bp.post("/refresh")
def refresh():
    cfg = current_app.config["AUTH"]
    try:
        user, new_raw = svc.rotate_refresh(
            get_session(), request.cookies.get(COOKIE), cfg.refresh_token_days
        )
    except Unauthorized as exc:
        resp = jsonify(error_body(exc.code, exc.message))
        resp.status_code = 401
        return _clear_cookie(resp)
    return _token_response(user, new_raw)


@bp.post("/logout")
def logout():
    svc.revoke_family(get_session(), request.cookies.get(COOKIE))
    return _clear_cookie(make_response("", 204))


@bp.get("/me")
@require_auth()
def me():
    user = get_session().get(User, uuid.UUID(g.user_id))
    if user is None or not user.is_active:
        raise Unauthorized("Account disabled", code="ACCOUNT_DISABLED")
    return jsonify(svc.user_dict(user))
