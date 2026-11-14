import os
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from auth.dependencies import bearer
from auth.security import session_secret
from database.models import CustomerSession
from database.session import get_db
from database.tenant import set_customer


@dataclass(frozen=True)
class CustomerPrincipal:
    session_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    contact_id: UUID
    verified: bool


def app_origins():
    return {
        value.strip().rstrip("/")
        for value in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if value.strip()
    }


def validate_origin(request, allowed, requested=None):
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") not in app_origins():
        raise HTTPException(
            403, "Open customer support through the company's configured widget."
        )
    site = (requested or origin or sorted(app_origins())[0]).rstrip("/")
    if site not in app_origins() and site not in allowed:
        raise HTTPException(
            403, "This website is not allowed to embed this support widget."
        )
    return site


def encode_customer(principal, expires):
    return jwt.encode(
        {
            "type": "customer",
            "sub": str(principal.session_id),
            "wid": str(principal.workspace_id),
            "cid": str(principal.conversation_id),
            "contact": str(principal.contact_id),
            "role": "customer",
            "iat": datetime.now(timezone.utc),
            "exp": expires,
        },
        session_secret(),
        algorithm="HS256",
    )


async def get_customer(
    request: Request, credentials=Depends(bearer), db: AsyncSession = Depends(get_db)
):
    if credentials is None:
        raise HTTPException(401, "Start a customer support session first.")
    try:
        claims = jwt.decode(
            credentials.credentials,
            session_secret(),
            algorithms=["HS256"],
            options={"require": ["type", "exp", "sub", "wid", "cid", "contact"]},
        )
        if claims["type"] != "customer":
            raise ValueError()
        sid, wid, cid, contact = (
            UUID(claims[k]) for k in ("sub", "wid", "cid", "contact")
        )
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise HTTPException(
            401,
            "Your customer session expired. Sign in with your private access link again.",
        ) from None
    await set_customer(
        db, wid, sid, cid, for_write=request.method not in ("GET", "HEAD", "OPTIONS")
    )
    if not await db.scalar(
        text("SELECT resolveai_customer_session(:w,:s,:c)"),
        {"w": wid, "s": sid, "c": cid},
    ):
        raise HTTPException(401, "Your customer session is no longer active.")
    record = await db.get(CustomerSession, sid)
    if (
        record is None
        or record.revoked_at
        or record.expires_at <= datetime.now(timezone.utc)
        or record.workspace_id != wid
        or record.conversation_id != cid
        or record.contact_id != contact
    ):
        raise HTTPException(401, "Your customer session is no longer active.")
    if (
        request.headers.get("Origin")
        and request.headers["Origin"].rstrip("/") not in app_origins()
    ):
        raise HTTPException(403, "Use the configured customer support page.")
    return CustomerPrincipal(sid, wid, cid, contact, record.access_link_id is not None)
