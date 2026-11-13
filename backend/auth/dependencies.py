from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.security import session_secret
from database.models import AuthSession, User, WorkspaceMember
from database.session import get_db
from database.tenant import set_tenant

bearer = HTTPBearer(auto_error=False, scheme_name="ResolveAI access token")


@dataclass(frozen=True)
class Principal:
    user: User
    session_id: UUID
    workspace_id: UUID | None
    role: str | None


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    if not credentials:
        raise HTTPException(
            401, "Please sign in to continue.", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        claims = jwt.decode(
            credentials.credentials,
            session_secret(),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub", "sid", "type"]},
        )
        if claims["type"] != "access" or "wid" not in claims or "role" not in claims:
            raise ValueError("Wrong token type")
        user_id, family_id = UUID(claims["sub"]), UUID(claims["sid"])
        workspace_id = UUID(claims["wid"]) if claims["wid"] else None
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise HTTPException(
            401,
            "Your access token has expired or is invalid. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    family = await db.scalar(
        select(AuthSession).where(
            AuthSession.id == family_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(timezone.utc),
        )
    )
    user = await db.get(User, user_id)
    if family is None or user is None or not user.is_active:
        raise HTTPException(401, "Your session is no longer valid. Sign in again.")
    expected = request.headers.get("X-Workspace-ID")
    if expected and expected != str(workspace_id):
        raise HTTPException(
            404, "Workspace changed. Select the intended workspace and retry."
        )
    from resolveai.limits import rate_limit

    await rate_limit("user", str(user_id), 120, 60)
    path = request.url.path
    tenant_write = (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and path not in ("/api/v1/workspaces", "/api/v1/invitations/accept")
        and not path.endswith(("/switch", "/ask", "/knowledge/search"))
    )
    await set_tenant(db, workspace_id, user_id, for_write=tenant_write)
    role = None
    if workspace_id:
        role = await db.scalar(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if role is None:
            raise HTTPException(
                401, "Your workspace access changed. Refresh your session."
            )
    db.info["active_workspace_id"] = str(workspace_id) if workspace_id else None
    db.info["principal_role"] = role
    db.info["session_id"] = str(family_id)
    db.info["request_id"] = getattr(request.state, "request_id", None)
    return Principal(user, family_id, workspace_id, role)


async def get_current_user(principal: Principal = Depends(get_principal)):
    return principal.user


def require_workspace(principal: Principal):
    if principal.workspace_id is None:
        raise HTTPException(409, "Create or select a workspace first.")
    return principal.workspace_id


def require_admin(principal: Principal):
    require_workspace(principal)
    if principal.role not in ("owner", "admin"):
        raise HTTPException(
            403, "Only workspace owners and admins can perform this operation."
        )
