from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User
from .passwords import hash_password, needs_rehash, verify_password

# Keep password helpers importable from app.auth for existing internal callers.
__all__ = ["hash_password", "verify_password", "authenticate_user", "create_access_token"]
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
# Equal-cost password check for an unknown username. This is not an account.
_dummy_password_hash = hash_password("not-an-account-password")


def credential_fingerprint(user: User) -> str:
    """Invalidate credentials after password changes, resets, or account replacement."""
    key = get_settings().security.jwt_secret.encode("utf-8")
    value = f"timeboardapp-credential-v1:{user.id}:{user.hashed_password}".encode("utf-8")
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def authenticate_user(db: Session, username_or_email: str, password: str) -> Optional[User]:
    # Authentication must never create or promote an administrator. Recovery is
    # an explicit local `python -m app.cli reset-admin` operation.
    ident = (username_or_email or "").strip().lower()
    user = db.query(User).filter(or_(func.lower(User.username) == ident, func.lower(User.email) == ident)).first() if ident else None
    if not verify_password(password, user.hashed_password if user else _dummy_password_hash):
        return None
    if user and needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def create_access_token(*, subject: str, is_admin: bool, user: User, expires_minutes: int = 60 * 24) -> str:
    if not 1 <= expires_minutes <= 60 * 24:
        raise ValueError("Access token lifetime must be between 1 and 1440 minutes")
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user.id), "username": subject, "iat": now, "nbf": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "iss": "timeboardapp", "aud": "timeboardapp-api", "kind": "access",
        "pv": credential_fingerprint(user),
    }
    # Roles are deliberately not trusted from token claims; always read the DB.
    return jwt.encode(claims, get_settings().security.jwt_secret, algorithm="HS256")


def _decode_token(token: str) -> dict:
    return jwt.decode(
        token, get_settings().security.jwt_secret, algorithms=["HS256"],
        issuer="timeboardapp", audience="timeboardapp-api",
        options={"require": ["exp", "sub", "iat", "nbf", "pv", "kind"]},
    )


def get_current_user_api(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = _decode_token(token)
        if payload["kind"] != "access":
            raise error
        user = db.get(User, int(payload["sub"]))
        if not user or not hmac.compare_digest(str(payload["pv"]), credential_fingerprint(user)):
            raise error
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise error from None
    return user


def require_admin_api(current_user: User = Depends(get_current_user_api)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user


def session_user(request: Request, db: Session) -> User | None:
    try:
        user = db.get(User, int(request.session.get("user_id", 0)))
        fingerprint = str(request.session.get("credential_fingerprint", ""))
        if user and hmac.compare_digest(fingerprint, credential_fingerprint(user)):
            return user
    except (ValueError, TypeError):
        pass
    if request.session.get("user_id"):
        request.session.clear()
    return None


def get_current_user_session(request: Request, db: Session = Depends(get_db)) -> User:
    user = session_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin_session(current_user: User = Depends(get_current_user_session)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user
