import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header, HTTPException, Request, Response
from pwdlib import PasswordHash

ACCESS_SECONDS = 15 * 60
REFRESH_SECONDS = 30 * 24 * 60 * 60
REFRESH_COOKIE = "resolveai_refresh"
CSRF_COOKIE = "resolveai_csrf"
PASSWORD_HASHER = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def session_secret():
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 32 or secret.startswith("replace-with"):
        raise HTTPException(
            503,
            "Set JWT_SECRET_KEY to a random secret of at least 32 characters in backend/.env.",
        )
    return secret


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def secure_cookie():
    return os.getenv("COOKIE_SECURE", "false").lower() == "true"


def set_refresh_cookie(response: Response, value: str, seconds=REFRESH_SECONDS):
    response.set_cookie(
        REFRESH_COOKIE,
        value,
        max_age=seconds,
        httponly=True,
        secure=secure_cookie(),
        samesite="lax",
        path="/api/v1/auth",
    )
    response.delete_cookie("morrow_session", path="/")
    response.delete_cookie("morrow_csrf", path="/")
    response.headers["Cache-Control"] = "no-store"


def clear_cookies(response: Response):
    response.delete_cookie(
        REFRESH_COOKIE,
        path="/api/v1/auth",
        secure=secure_cookie(),
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", secure=secure_cookie(), samesite="strict"
    )
    response.delete_cookie("morrow_session", path="/")
    response.delete_cookie("morrow_csrf", path="/")
    response.headers["Cache-Control"] = "no-store"


def require_csrf(request: Request, x_csrf_token: str | None = Header(default=None)):
    if not request.url.path.startswith("/api/v1/auth/") and request.headers.get(
        "Authorization", ""
    ).startswith("Bearer "):
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = x_csrf_token or ""
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(
            403, "The security token is missing or invalid. Refresh and try again."
        )


def encode_access(user, family, workspace_id=None, role=None):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user.id),
            "sid": str(family.id),
            "wid": str(workspace_id) if workspace_id else None,
            "role": role,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(seconds=ACCESS_SECONDS),
        },
        session_secret(),
        algorithm="HS256",
    )
