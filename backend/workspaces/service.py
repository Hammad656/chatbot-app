import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from auth.dependencies import require_admin, require_workspace
from auth.security import token_hash
from auth.service import switch_workspace
from auth.service import access_response
from database.models import (
    SupportAudit,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)
from database.tenant import set_tenant
from workspaces.schemas import Settings


def audit(db, workspace_id, actor, event, **details):
    db.add(
        SupportAudit(
            workspace_id=workspace_id,
            actor_id=actor,
            event=event,
            details={**details, "request_id": db.info.get("request_id")},
        )
    )


async def create(db, principal, body):
    import os

    if body.ai_mode == "gemini" and not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(
            503,
            "Gemini workspaces require GEMINI_API_KEY. Local demo mode needs no provider keys.",
        )
    await db.scalar(select(User).where(User.id == principal.user.id).with_for_update())
    count = await db.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.owner_id == principal.user.id)
    )
    if count >= 10:
        raise HTTPException(
            409, "This account already owns the maximum of ten workspaces."
        )
    slug = (
        (re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "workspace")[:80]
        + "-"
        + secrets.token_hex(4)
    )
    workspace = Workspace(
        name=body.name,
        slug=slug,
        owner_id=principal.user.id,
        ai_mode=body.ai_mode,
        embedding_model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        settings=Settings().model_dump(),
    )
    db.add(workspace)
    await db.flush()
    await set_tenant(db, workspace.id, principal.user.id)
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id, user_id=principal.user.id, role="owner"
        )
    )
    audit(db, workspace.id, principal.user.id, "workspace.created", mode=body.ai_mode)
    await db.commit()
    return {
        "id": workspace.id,
        "name": workspace.name,
        "slug": workspace.slug,
        "role": "owner",
        "ai_mode": workspace.ai_mode,
        "created_at": workspace.created_at,
    }


async def settings_view(db, principal):
    workspace = await db.get(Workspace, require_workspace(principal))
    if workspace is None:
        raise HTTPException(404, "Workspace not found.")
    return {
        "id": workspace.id,
        "name": workspace.name,
        "slug": workspace.slug,
        "role": principal.role,
        "ai_mode": workspace.ai_mode,
        "embedding_model": workspace.embedding_model,
        "allowed_origins": workspace.allowed_origins,
        "widget_public_key": (
            workspace.widget_public_key
            if principal.role in ("owner", "admin")
            else None
        ),
        "settings": Settings.model_validate(
            {
                key: value
                for key, value in workspace.settings.items()
                if key in Settings.model_fields
            }
        ).model_dump(),
    }


async def update_settings(db, principal, body):
    require_admin(principal)
    workspace = await db.scalar(
        select(Workspace)
        .where(Workspace.id == principal.workspace_id)
        .with_for_update()
    )
    if body.name is not None:
        workspace.name = body.name
    if body.allowed_origins is not None:
        workspace.allowed_origins = body.allowed_origins
    if body.settings is not None:
        workspace.settings = {
            **workspace.settings,
            **body.settings.model_dump(exclude_unset=True),
        }
    audit(
        db,
        workspace.id,
        principal.user.id,
        "workspace.settings_updated",
        fields=list(body.model_fields_set),
    )
    await db.commit()
    return await settings_view(db, principal)


async def list_members(db, principal):
    require_admin(principal)
    rows = (
        await db.execute(
            select(WorkspaceMember, User.email, User.full_name)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == principal.workspace_id)
            .order_by(WorkspaceMember.created_at, WorkspaceMember.id)
        )
    ).all()
    return [
        {
            "id": m.id,
            "user_id": m.user_id,
            "email": email,
            "full_name": name,
            "role": m.role,
            "created_at": m.created_at,
        }
        for m, email, name in rows
    ]


async def change_member(db, principal, member_id, role=None):
    require_admin(principal)
    target = await db.scalar(
        select(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == principal.workspace_id,
            WorkspaceMember.id == member_id,
        )
        .with_for_update()
    )
    if not target:
        raise HTTPException(404, "Member not found.")
    if target.role == "owner":
        raise HTTPException(409, "The workspace owner cannot be removed or demoted.")
    if target.user_id == principal.user.id:
        raise HTTPException(
            409, "Ask another workspace owner or admin to change your own role."
        )
    audit(
        db,
        principal.workspace_id,
        principal.user.id,
        "member.role_changed" if role else "member.removed",
        member_id=str(target.id),
        user_id=str(target.user_id),
        previous_role=target.role,
        role=role,
    )
    if role:
        target.role = role
    else:
        await db.delete(target)
    await db.commit()


async def invite(db, principal, body):
    require_admin(principal)
    await db.scalar(
        select(Workspace)
        .where(Workspace.id == principal.workspace_id)
        .with_for_update()
    )
    email = str(body.email).lower()
    if await db.scalar(
        select(WorkspaceMember.user_id)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == principal.workspace_id, User.email == email
        )
    ):
        raise HTTPException(409, "This account is already a workspace member.")
    now = datetime.now(timezone.utc)
    pending = (
        await db.scalars(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.workspace_id == principal.workspace_id,
                WorkspaceInvitation.email == email,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
        )
    ).all()
    for old in pending:
        old.revoked_at = now
    raw = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(
        workspace_id=principal.workspace_id,
        email=email,
        role=body.role,
        token_hash=token_hash(raw),
        created_by=principal.user.id,
        expires_at=now + timedelta(days=7),
    )
    db.add(invitation)
    await db.flush()
    audit(
        db,
        principal.workspace_id,
        principal.user.id,
        "member.invited",
        invitation_id=str(invitation.id),
        role=body.role,
    )
    await db.commit()
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
        "invitation_token": raw,
        "delivery": "Copy the invitation link and share it with this teammate. No email was sent.",
    }


async def list_invites(db, principal):
    require_admin(principal)
    rows = (
        await db.scalars(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == principal.workspace_id)
            .order_by(WorkspaceInvitation.created_at.desc())
            .limit(100)
        )
    ).all()
    now = datetime.now(timezone.utc)
    return [
        {
            "id": i.id,
            "email": i.email,
            "role": i.role,
            "created_at": i.created_at,
            "expires_at": i.expires_at,
            "status": (
                "revoked"
                if i.revoked_at
                else (
                    "accepted"
                    if i.accepted_at
                    else "expired" if i.expires_at <= now else "pending"
                )
            ),
        }
        for i in rows
    ]


async def revoke_invite(db, principal, invitation_id):
    require_admin(principal)
    row = await db.scalar(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == principal.workspace_id,
            WorkspaceInvitation.id == invitation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Invitation not found.")
    row.revoked_at = datetime.now(timezone.utc)
    audit(
        db,
        principal.workspace_id,
        principal.user.id,
        "member.invitation_revoked",
        invitation_id=str(row.id),
    )
    await db.commit()


async def accept_invitation(db, principal, raw):
    # The narrow, audited database function permits only a token's named recipient
    # to cross from no membership to membership. It never exposes other invites.
    try:
        workspace_id = await db.scalar(
            text("SELECT resolveai_accept_invitation(:digest,:actor)"),
            {"digest": token_hash(raw), "actor": principal.user.id},
        )
    except DBAPIError as error:
        await db.rollback()
        if getattr(error.orig, "sqlstate", None) == "P0001":
            raise HTTPException(
                404,
                "Invitation is invalid, expired, revoked, or belongs to another email address.",
            ) from None
        raise
    return await switch_workspace(db, principal, workspace_id)


async def delete_workspace(db, principal, workspace_id, body):
    import logging
    from starlette.concurrency import run_in_threadpool
    from database.models import AuthSession, BusinessDocument
    from resolveai.storage import delete_upload

    if principal.workspace_id != workspace_id:
        raise HTTPException(404, "Workspace not found.")
    workspace = await db.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    if principal.role != "owner" or workspace.owner_id != principal.user.id:
        raise HTTPException(403, "Only the workspace owner can delete this workspace.")
    if body.confirmation_name != workspace.name:
        raise HTTPException(409, "Type the exact workspace name to confirm deletion.")
    keys = list(
        (
            await db.scalars(
                select(BusinessDocument.storage_key).where(
                    BusinessDocument.workspace_id == workspace_id,
                    BusinessDocument.storage_key.is_not(None),
                )
            )
        ).all()
    )
    from database.models import AuthenticationEvent

    db.add(
        AuthenticationEvent(
            user_id=principal.user.id,
            event="workspace.deleted",
            subject_id=str(workspace_id),
            request_id=db.info.get("request_id"),
            created_at=datetime.now(timezone.utc),
        )
    )
    await db.delete(workspace)
    await db.commit()
    for key in keys:
        try:
            await run_in_threadpool(delete_upload, key)
        except OSError:
            logging.getLogger(__name__).warning(
                "Private file cleanup needs retry after workspace deletion."
            )
    family = await db.scalar(
        select(AuthSession)
        .where(AuthSession.id == principal.session_id)
        .execution_options(populate_existing=True)
    )
    result = await access_response(db, principal.user, family)
    await db.commit()
    return result
