from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import Principal, get_principal
from auth.schemas import TokenResponse
from auth.service import switch_workspace
from database.session import get_db
from workspaces import service
from workspaces.schemas import (
    InvitationAccept,
    InvitationCreate,
    MemberUpdate,
    WorkspaceUpdate,
    WorkspaceDelete,
)

router = APIRouter(prefix="/api/v1", tags=["Workspaces and members"])


@router.delete("/workspaces/{workspace_id}", response_model=TokenResponse)
async def delete_workspace(
    workspace_id: UUID,
    body: WorkspaceDelete,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    return await service.delete_workspace(db, principal, workspace_id, body)


@router.post("/workspaces/{workspace_id}/switch", response_model=TokenResponse)
async def switch(
    workspace_id: UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    return await switch_workspace(db, principal, workspace_id)


@router.get("/workspace/settings")
async def settings(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    return await service.settings_view(db, principal)


@router.patch("/workspace/settings")
async def update_settings(
    body: WorkspaceUpdate,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_settings(db, principal, body)


@router.get("/members")
async def members(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    return await service.list_members(db, principal)


@router.patch("/members/{member_id}", status_code=204)
async def update_member(
    member_id: UUID,
    body: MemberUpdate,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    await service.change_member(db, principal, member_id, body.role)
    return Response(status_code=204)


@router.delete("/members/{member_id}", status_code=204)
async def remove_member(
    member_id: UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    await service.change_member(db, principal, member_id)
    return Response(status_code=204)


@router.post("/members/invite", status_code=201)
async def invite(
    body: InvitationCreate,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    return await service.invite(db, principal, body)


@router.get("/invitations")
async def invitations(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    return await service.list_invites(db, principal)


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invite(
    invitation_id: UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    await service.revoke_invite(db, principal, invitation_id)
    return Response(status_code=204)


@router.post("/invitations/accept", response_model=TokenResponse)
async def accept_invite(
    body: InvitationAccept,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    return await service.accept_invitation(db, principal, body.token)
