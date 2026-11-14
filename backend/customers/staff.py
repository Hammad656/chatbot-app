"""Staff operations for customer identities, conversations and approval queues."""

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import Principal, get_principal, require_admin, require_workspace
from auth.security import token_hash
from customers.router import message_view
from customers.schemas import AccessLink
from database.models import (
    ActionRequest,
    CustomerAccessLink,
    CustomerContact,
    CustomerConversation,
    CustomerMessage,
    CustomerSession,
    ReplyDraft,
    SupportAudit,
    SupportTicket,
    WorkspaceMember,
)
from database.pagination import page
from database.repository import TenantRepository
from database.session import get_db
from resolveai.actions import review
from resolveai.router import detail, fields, ticket_in_workspace
from resolveai.schemas import Review, StrictModel

router = APIRouter(prefix="/api/v1", tags=["Customer operations and approvals"])


class TicketUpdate(StrictModel):
    priority: str | None = Field(default=None, pattern=r"^(low|normal|high|urgent)$")
    status: str | None = Field(default=None, pattern=r"^(open|resolved)$")
    assigned_to: UUID | None = None


class DraftEdit(StrictModel):
    content: str = Field(min_length=1, max_length=12000)
    expected_revision: int = Field(ge=1)


@router.post("/customer-access-links", status_code=201)
async def create_access(
    body: AccessLink,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    require_admin(principal)
    email = str(body.email).lower()
    contact = await db.scalar(
        select(CustomerContact).where(
            CustomerContact.workspace_id == principal.workspace_id,
            CustomerContact.email == email,
        )
    )
    if contact is None:
        contact = CustomerContact(
            workspace_id=principal.workspace_id, email=email, name=body.name
        )
        db.add(contact)
        await db.flush()
    else:
        contact.name = body.name
    old = list(
        (
            await db.scalars(
                select(CustomerAccessLink).where(
                    CustomerAccessLink.workspace_id == principal.workspace_id,
                    CustomerAccessLink.contact_id == contact.id,
                    CustomerAccessLink.revoked_at.is_(None),
                )
            )
        ).all()
    )
    for link in old:
        link.revoked_at = datetime.now(timezone.utc)
    raw = secrets.token_urlsafe(48)
    link = CustomerAccessLink(
        workspace_id=principal.workspace_id,
        contact_id=contact.id,
        token_hash=token_hash(raw),
        created_by=principal.user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(link)
    await db.flush()
    db.add(
        SupportAudit(
            workspace_id=principal.workspace_id,
            actor_id=principal.user.id,
            event="customer.access_link_created",
            details={"link_id": str(link.id), "contact_id": str(contact.id)},
        )
    )
    await db.commit()
    return {
        "id": link.id,
        "contact_id": contact.id,
        "access_token": raw,
        "expires_at": link.expires_at,
        "delivery": "Share this private link with the customer through a trusted channel. No email was sent.",
    }


@router.get("/customer-access-links")
async def access_links(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    rows = (
        await db.execute(
            select(CustomerAccessLink, CustomerContact.email, CustomerContact.name)
            .join(CustomerContact, CustomerContact.id == CustomerAccessLink.contact_id)
            .where(CustomerAccessLink.workspace_id == principal.workspace_id)
            .order_by(CustomerAccessLink.created_at.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "email": email,
            "name": name,
            "expires_at": row.expires_at,
            "revoked": row.revoked_at is not None,
        }
        for row, email, name in rows
    ]


@router.delete("/customer-access-links/{link_id}", status_code=204)
async def revoke_access(
    link_id: UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    require_admin(principal)
    link = await TenantRepository(db).get(CustomerAccessLink, link_id, lock=True)
    link.revoked_at = datetime.now(timezone.utc)
    await db.execute(
        update(CustomerSession)
        .where(
            CustomerSession.workspace_id == principal.workspace_id,
            CustomerSession.access_link_id == link.id,
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.add(
        SupportAudit(
            workspace_id=principal.workspace_id,
            actor_id=principal.user.id,
            event="customer.access_link_revoked",
            details={"link_id": str(link.id)},
        )
    )
    await db.commit()


@router.get("/customer-conversations")
async def conversations(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    require_workspace(principal)
    return await page(
        db,
        CustomerConversation,
        lambda row: fields(
            row, "id", "title", "status", "contact_id", "created_at", "updated_at"
        ),
        limit=limit,
        cursor=cursor,
    )


@router.get("/customer-conversations/{conversation_id}")
async def conversation(
    conversation_id: UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    require_workspace(principal)
    row = await TenantRepository(db).get(CustomerConversation, conversation_id)
    contact = await TenantRepository(db).get(CustomerContact, row.contact_id)
    messages = list(
        (
            await db.scalars(
                select(CustomerMessage)
                .where(
                    CustomerMessage.workspace_id == principal.workspace_id,
                    CustomerMessage.conversation_id == row.id,
                )
                .order_by(CustomerMessage.created_at.desc(), CustomerMessage.id.desc())
                .limit(100)
            )
        ).all()
    )
    tickets = list(
        (
            await db.scalars(
                select(SupportTicket.id).where(
                    SupportTicket.workspace_id == principal.workspace_id,
                    SupportTicket.customer_conversation_id == row.id,
                )
            )
        ).all()
    )
    return {
        **fields(row, "id", "title", "status", "created_at"),
        "contact": fields(contact, "name", "email"),
        "messages": [message_view(m) for m in reversed(messages)],
        "ticket_ids": tickets,
    }


@router.get("/actions")
async def actions(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    require_workspace(principal)
    return await page(
        db,
        ActionRequest,
        lambda row: fields(
            row,
            "id",
            "ticket_id",
            "draft_id",
            "revision",
            "kind",
            "risk",
            "status",
            "reply",
            "error",
            "result",
            "created_at",
        ),
        limit=limit,
        cursor=cursor,
        filters=(ActionRequest.status == status,) if status else (),
    )


@router.post("/actions/{action_id}/{decision}")
async def decide_action(
    action_id: UUID,
    decision: str,
    body: Review,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    if decision not in ("approve", "reject") or decision != body.decision:
        raise HTTPException(400, "Use approve or reject with a matching decision.")
    action = await TenantRepository(db).get(ActionRequest, action_id)
    ticket = await ticket_in_workspace(
        db, require_workspace(principal), action.ticket_id, lock=True
    )
    if action.revision != body.expected_revision:
        raise HTTPException(409, "Review the latest draft revision.")
    return await review(db, ticket, body, principal.user, principal.role)


@router.patch("/drafts/{draft_id}")
async def edit_draft(
    draft_id: UUID,
    body: DraftEdit,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    draft = await TenantRepository(db).get(ReplyDraft, draft_id, lock=True)
    ticket = await ticket_in_workspace(
        db, require_workspace(principal), draft.ticket_id, lock=True
    )
    if (
        ticket.revision != body.expected_revision
        or draft.revision != ticket.revision
        or ticket.status != "awaiting_approval"
        or draft.status != "proposed"
    ):
        raise HTTPException(409, "This draft changed or was already reviewed.")
    draft.content, ticket.draft = body.content, body.content
    db.add(
        SupportAudit(
            workspace_id=principal.workspace_id,
            actor_id=principal.user.id,
            ticket_id=ticket.id,
            event="draft.edited",
            details={"draft_id": str(draft.id)},
        )
    )
    await db.commit()
    return {"id": draft.id, "content": draft.content, "revision": draft.revision}


@router.post("/drafts/{draft_id}/{decision}")
async def decide_draft(
    draft_id: UUID,
    decision: str,
    body: Review,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    if decision not in ("approve", "reject") or decision != body.decision:
        raise HTTPException(400, "Use approve or reject with a matching decision.")
    draft = await TenantRepository(db).get(ReplyDraft, draft_id)
    ticket = await ticket_in_workspace(
        db, require_workspace(principal), draft.ticket_id, lock=True
    )
    if draft.revision != body.expected_revision:
        raise HTTPException(409, "Review the latest draft revision.")
    return await review(db, ticket, body, principal.user, principal.role)


@router.patch("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: UUID,
    body: TicketUpdate,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    ticket = await ticket_in_workspace(
        db, require_workspace(principal), ticket_id, lock=True
    )
    if "assigned_to" in body.model_fields_set:
        if body.assigned_to and not await db.scalar(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == principal.workspace_id,
                WorkspaceMember.user_id == body.assigned_to,
            )
        ):
            raise HTTPException(404, "Workspace member not found.")
        ticket.assigned_to = body.assigned_to
    if body.priority:
        ticket.priority = body.priority
    if body.status:
        if ticket.status in ("processing", "delivery_pending"):
            raise HTTPException(
                409, "Wait for the current workflow before changing status."
            )
        ticket.status = body.status
        ticket.revision += 1
        for action in (
            await db.scalars(
                select(ActionRequest).where(
                    ActionRequest.workspace_id == principal.workspace_id,
                    ActionRequest.ticket_id == ticket.id,
                    ActionRequest.status == "proposed",
                )
            )
        ).all():
            action.status = "superseded"
        if ticket.customer_conversation_id:
            conversation = await db.get(
                CustomerConversation, ticket.customer_conversation_id
            )
            conversation.status = (
                "resolved" if body.status == "resolved" else "waiting_for_agent"
            )
    db.add(
        SupportAudit(
            workspace_id=principal.workspace_id,
            ticket_id=ticket.id,
            actor_id=principal.user.id,
            event="ticket.updated",
            details={"fields": list(body.model_fields_set)},
        )
    )
    await db.commit()
    await db.refresh(ticket)
    return await detail(db, ticket)
