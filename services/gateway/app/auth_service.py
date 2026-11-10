"""Login, refresh-token rotation and user rules. Pure functions over a Session."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from rp_shared.auth import hash_password, verify_password
from rp_shared.errors import Conflict, Unauthorized, ValidationFailed
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import AuditLog, RefreshToken, User, utcnow

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
# Verified when the account does not exist, so response time doesn't reveal valid emails.
_DUMMY_HASH = hash_password("not-a-real-password")


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ValidationFailed("Invalid email address", details=[{"field": "email"}])
    return email


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD or len(password) > 128:
        raise ValidationFailed(
            f"Password must be {MIN_PASSWORD} to 128 characters",
            details=[{"field": "password"}],
        )


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def audit(
    session: Session,
    action: str,
    user_id: uuid.UUID | str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    if isinstance(user_id, str):
        try:
            user_id = uuid.UUID(user_id)
        except ValueError:
            user_id = None
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            meta=meta,
        )
    )


def user_dict(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": aware(u.created_at).isoformat(),
        "last_login": aware(u.last_login).isoformat() if u.last_login else None,
    }


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_refresh(
    session: Session, user: User, days: int, family_id: uuid.UUID | None = None
) -> str:
    raw = secrets.token_urlsafe(48)
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=_hash_token(raw),
            expires_at=utcnow() + timedelta(days=days),
        )
    )
    return raw


def authenticate(session: Session, email: str, password: str, ip: str | None) -> User:
    """Return the active user or raise a generic Unauthorized (no account enumeration)."""
    email = email.strip().lower()
    user = session.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, password)
    if not user or not ok or not user.is_active:
        audit(session, "auth.login_failed", user.id if user else None, meta={"ip": ip})
        session.commit()
        raise Unauthorized("Invalid email or password", code="INVALID_CREDENTIALS")
    return user


def login(session: Session, user: User, refresh_days: int, ip: str | None) -> str:
    user.last_login = utcnow()
    raw = _issue_refresh(session, user, refresh_days)
    audit(session, "auth.login", user.id, meta={"ip": ip})
    session.commit()
    return raw


def rotate_refresh(session: Session, raw: str | None, refresh_days: int) -> tuple[User, str]:
    """Exchange a refresh token for a new one. Presenting an already-rotated token means it
    leaked (or the client is racing): revoke the whole family and make the user log in again."""
    if not raw:
        raise Unauthorized("No refresh token", code="REFRESH_MISSING")
    token = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw)))
    if token is None:
        raise Unauthorized("Invalid refresh token", code="REFRESH_INVALID")
    if token.revoked:
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id)
            .values(revoked=True)
        )
        audit(session, "auth.refresh_reuse_detected", token.user_id)
        session.commit()
        raise Unauthorized("Refresh token reuse detected", code="REFRESH_REUSED")
    user = session.get(User, token.user_id)
    if aware(token.expires_at) <= utcnow() or user is None or not user.is_active:
        token.revoked = True
        session.commit()
        raise Unauthorized("Refresh token expired", code="REFRESH_EXPIRED")
    token.revoked = True
    new_raw = _issue_refresh(session, user, refresh_days, family_id=token.family_id)
    session.commit()
    return user, new_raw


def revoke_family(session: Session, raw: str | None) -> None:
    if not raw:
        return
    token = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw)))
    if token is not None:
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id)
            .values(revoked=True)
        )
        audit(session, "auth.logout", token.user_id)
        session.commit()


def revoke_all(session: Session, user_id: uuid.UUID) -> None:
    session.execute(
        update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked=True)
    )


def active_admin_count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active)
    )


def ensure_not_last_admin(session: Session, user: User, new_role: str, new_active: bool) -> None:
    losing_admin = (
        user.role == "admin" and user.is_active and (new_role != "admin" or not new_active)
    )
    if losing_admin and active_admin_count(session) <= 1:
        raise Conflict("Cannot demote or deactivate the last active admin", code="LAST_ADMIN")


def create_user(session: Session, email: str, password: str, display_name: str, role: str) -> User:
    email = normalize_email(email)
    check_password_policy(password)
    if session.scalar(select(User.id).where(User.email == email)):
        raise Conflict("A user with this email already exists", code="EMAIL_TAKEN")
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name.strip(),
        role=role,
    )
    session.add(user)
    session.flush()
    return user


def bootstrap_admin(session: Session, email: str | None, password: str | None) -> bool:
    """Create the first admin from ADMIN_EMAIL / ADMIN_PASSWORD when the table is empty."""
    if session.scalar(select(func.count()).select_from(User)):
        return False
    if not email or not password:
        return False
    user = create_user(session, email, password, "Administrator", "admin")
    audit(session, "user.bootstrap_admin", user.id, "user", str(user.id))
    session.commit()
    return True
