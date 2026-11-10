import uuid
from typing import Literal

from flask import Blueprint, g, jsonify, request
from pydantic import BaseModel, Field
from rp_shared.auth import hash_password, require_service_token
from rp_shared.errors import NotFound, ValidationFailed
from sqlalchemy import select

from .. import auth_service as svc
from ..db import get_session
from ..models import AuditLog, User
from ..security import parse_body, require_auth

bp = Blueprint("users", __name__, url_prefix="/users")
audit_bp = Blueprint("audit", __name__, url_prefix="/audit")

Role = Literal["admin", "designer", "operator", "viewer"]


class UserCreate(BaseModel):
    email: str
    password: str
    display_name: str = Field(min_length=1, max_length=100)
    role: Role = "operator"


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = None


def _get(session, user_id: str) -> User:
    try:
        user = session.get(User, uuid.UUID(user_id))
    except ValueError:
        user = None
    if user is None:
        raise NotFound("User not found")
    return user


@bp.get("")
@require_auth("admin")
def list_users():
    users = get_session().scalars(select(User).order_by(User.created_at)).all()
    return jsonify(items=[svc.user_dict(u) for u in users], total=len(users))


@bp.post("")
@require_auth("admin")
def create_user():
    body = parse_body(UserCreate)
    session = get_session()
    user = svc.create_user(session, body.email, body.password, body.display_name, body.role)
    svc.audit(session, "user.create", g.user_id, "user", str(user.id), {"role": user.role})
    session.commit()
    return jsonify(svc.user_dict(user)), 201


@bp.get("/<user_id>")
@require_auth("admin")
def get_user(user_id: str):
    return jsonify(svc.user_dict(_get(get_session(), user_id)))


@bp.patch("/<user_id>")
@require_auth("admin")
def update_user(user_id: str):
    body = parse_body(UserUpdate)
    session = get_session()
    user = _get(session, user_id)
    new_role = body.role or user.role
    new_active = user.is_active if body.is_active is None else body.is_active
    svc.ensure_not_last_admin(session, user, new_role, new_active)

    changed: list[str] = []
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
        changed.append("display_name")
    if new_role != user.role:
        user.role = new_role
        changed.append("role")
    if new_active != user.is_active:
        user.is_active = new_active
        changed.append("is_active")
    if body.password is not None:
        svc.check_password_policy(body.password)
        user.password_hash = hash_password(body.password)
        changed.append("password")
    if "password" in changed or not user.is_active:
        svc.revoke_all(session, user.id)  # force re-login everywhere
    svc.audit(session, "user.update", g.user_id, "user", str(user.id), {"changed": changed})
    session.commit()
    return jsonify(svc.user_dict(user))


@audit_bp.post("")
@require_service_token
def record_audit():
    """Written by the gateway after each state-changing call. Service token only: the gateway
    never exposes this route to browsers."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("action"), str):
        raise ValidationFailed("'action' is required")
    meta = body.get("meta")
    fields = {k: body.get(k) for k in ("user_id", "resource_type", "resource_id")}
    if (
        not body["action"]
        or len(body["action"]) > 80
        or not (meta is None or isinstance(meta, dict))
    ):
        raise ValidationFailed("Invalid audit entry")
    if any(v is not None and not isinstance(v, str) for v in fields.values()):
        raise ValidationFailed("Invalid audit entry")
    session = get_session()
    svc.audit(
        session,
        body["action"],
        fields["user_id"],
        fields["resource_type"],
        fields["resource_id"],
        meta,
    )
    session.commit()
    return "", 204


@audit_bp.get("")
@require_auth("admin")
def audit_log():
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    rows = get_session().scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return jsonify(
        items=[
            {
                "id": r.id,
                "user_id": str(r.user_id) if r.user_id else None,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "meta": r.meta,
                "created_at": svc.aware(r.created_at).isoformat(),
            }
            for r in rows
        ]
    )
