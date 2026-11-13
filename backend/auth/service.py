import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Response
from sqlalchemy import select, update

from auth.schemas import TokenResponse, UserView
from auth.security import REFRESH_SECONDS, encode_access, set_refresh_cookie, token_hash
from database.models import (
    AuthenticationEvent,
    AuthSession,
    RefreshToken,
    SupportAudit,
    WorkspaceMember,
)
from database.tenant import set_tenant


def security_event(db, request, event, user_id=None, subject_id=None):
    db.add(
        AuthenticationEvent(
            user_id=user_id,
            event=event,
            subject_id=str(subject_id) if subject_id else None,
            created_at=datetime.now(timezone.utc),
            request_id=getattr(request.state, "request_id", None),
        )
    )


async def choose_membership(db, user, preferred=None):
    await set_tenant(db, actor_id=user.id)
    query = select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    if preferred:
        chosen = await db.scalar(query.where(WorkspaceMember.workspace_id == preferred))
        if chosen:
            return chosen
    return await db.scalar(
        query.order_by(WorkspaceMember.created_at, WorkspaceMember.workspace_id).limit(
            1
        )
    )


async def access_response(db, user, family):
    membership = await choose_membership(db, user, family.active_workspace_id)
    workspace_id, role = (
        (membership.workspace_id, membership.role) if membership else (None, None)
    )
    family.active_workspace_id = workspace_id
    await set_tenant(db, workspace_id, user.id)
    return TokenResponse(
        access_token=encode_access(user, family, workspace_id, role),
        user=UserView.model_validate(user).model_copy(
            update={"active_workspace_id": workspace_id, "role": role}
        ),
    )


async def issue_refresh(db, user, family, response):
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family.id,
            token_hash=token_hash(raw),
            expires_at=family.expires_at,
        )
    )
    set_refresh_cookie(
        response,
        raw,
        max(1, int((family.expires_at - datetime.now(timezone.utc)).total_seconds())),
    )


async def new_session(db, user, request, response: Response):
    family = AuthSession(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=REFRESH_SECONDS),
    )
    db.add(family)
    await db.flush()
    result = await access_response(db, user, family)
    await issue_refresh(db, user, family, response)
    security_event(db, request, "auth.login", user.id)
    if family.active_workspace_id:
        db.add(
            SupportAudit(
                workspace_id=family.active_workspace_id,
                actor_id=user.id,
                event="auth.login",
                details={"request_id": getattr(request.state, "request_id", None)},
            )
        )
    await db.commit()
    return result


async def revoke_family(db, family):
    family.revoked_at = datetime.now(timezone.utc)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=family.revoked_at)
    )


async def switch_workspace(db, principal, workspace_id):
    # Switching is the sole authenticated route allowed to establish another tenant context.
    await set_tenant(db, workspace_id, principal.user.id, for_write=True)
    membership = await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == principal.user.id,
        )
    )
    if membership is None:
        raise HTTPException(404, "Workspace not found.")
    family = await db.scalar(
        select(AuthSession)
        .where(AuthSession.id == principal.session_id)
        .with_for_update()
    )
    family.active_workspace_id = workspace_id
    result = await access_response(db, principal.user, family)
    db.add(
        SupportAudit(
            workspace_id=workspace_id,
            actor_id=principal.user.id,
            event="workspace.switched",
            details={"request_id": db.info.get("request_id")},
        )
    )
    await db.commit()
    return result
