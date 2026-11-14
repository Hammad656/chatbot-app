"""Approved, durable, idempotent actions and customer reply delivery."""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from database.models import (
    ActionRequest,
    BusinessDocument,
    CustomerConversation,
    CustomerMessage,
    DemoOrder,
    ReplyDraft,
    SupportAudit,
    SupportDecision,
    SupportMessage,
    SupportTicket,
    User,
    WorkspaceMember,
)
from database.session import async_session_factory
from database.tenant import set_tenant


async def proposal(db, ticket):
    action = await db.scalar(
        select(ActionRequest).where(
            ActionRequest.ticket_id == ticket.id,
            ActionRequest.revision == ticket.revision,
        )
    )
    if action:
        return action
    draft = ReplyDraft(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        revision=ticket.revision,
        content=ticket.draft or "",
        sources=ticket.sources,
    )
    db.add(draft)
    await db.flush()
    kind = ticket.proposed_action.get("kind", "escalate")
    action = ActionRequest(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        draft_id=draft.id,
        revision=ticket.revision,
        kind=kind,
        risk="sensitive" if kind in ("refund", "replacement") else "write",
        arguments={**ticket.proposed_action, "sources": ticket.sources},
        reply=ticket.draft or "",
    )
    db.add(action)
    await db.flush()
    return action


async def validate_action(db, ticket, kind, arguments):
    ids = {UUID(source["document_id"]) for source in arguments.get("sources", [])}
    ready = set(
        (
            await db.scalars(
                select(BusinessDocument.id)
                .where(
                    BusinessDocument.workspace_id == ticket.workspace_id,
                    BusinessDocument.id.in_(ids),
                    BusinessDocument.status == "ready",
                )
                .with_for_update()
            )
        ).all()
    )
    if ids != ready or (kind in ("refund", "replacement") and not ids):
        raise HTTPException(
            409,
            "Policy evidence changed or was removed. Generate a new draft before approval.",
        )
    if kind not in ("refund", "replacement"):
        return None
    order = await db.scalar(
        select(DemoOrder)
        .where(
            DemoOrder.workspace_id == ticket.workspace_id,
            DemoOrder.id == UUID(arguments["order_id"]),
            DemoOrder.customer_email == ticket.customer_email,
        )
        .with_for_update()
    )
    if (
        not order
        or order.status not in ("delivered", "shipped")
        or not (order.refundable if kind == "refund" else order.replaceable)
        or order.amount_cents != arguments["amount_cents"]
    ):
        raise HTTPException(
            409, "The order is no longer eligible. Generate a new draft or escalate."
        )
    return order


async def review(db, ticket, body, user, role):
    from resolveai.router import detail

    sensitive = ticket.proposed_action.get("kind") in ("refund", "replacement")
    if sensitive and role not in ("owner", "admin"):
        raise HTTPException(403, "An administrator must review this sensitive action.")
    digest = hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json", exclude={"request_id"}), sort_keys=True
        ).encode()
    ).hexdigest()
    previous = await db.scalar(
        select(SupportDecision).where(
            SupportDecision.workspace_id == ticket.workspace_id,
            SupportDecision.request_id == body.request_id,
        )
    )
    if previous is None:
        previous = await db.scalar(
            select(SupportDecision).where(
                SupportDecision.ticket_id == ticket.id,
                SupportDecision.ticket_revision == body.expected_revision,
            )
        )
    if previous:
        if previous.ticket_id != ticket.id or previous.request_hash != digest:
            raise HTTPException(
                409,
                "This review/idempotency key was already used with different parameters.",
            )
        return {
            "replayed": True,
            "decision": {
                "id": previous.id,
                "decision": previous.decision,
                "action": previous.action,
                "result": previous.result,
            },
            "ticket": await detail(db, ticket),
        }
    if (
        ticket.status != "awaiting_approval"
        or ticket.revision != body.expected_revision
    ):
        raise HTTPException(
            409, "The ticket changed. Reload it and review the latest draft."
        )
    action = await proposal(db, ticket)
    draft = await db.get(ReplyDraft, action.draft_id)
    if body.decision == "approve":
        await validate_action(db, ticket, action.kind, action.arguments)
    decision = SupportDecision(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        ticket_revision=ticket.revision,
        request_id=body.request_id,
        request_hash=digest,
        decision=body.decision,
        action=action.kind,
        reply=body.reply,
        result={
            "queued": body.decision == "approve",
            "delivery": "queued" if body.decision == "approve" else "not_sent",
            "mock": sensitive,
        },
        actor_id=user.id,
    )
    db.add(decision)
    await db.flush()
    action.decision_id, action.reply = decision.id, body.reply
    draft.content = body.reply
    action.status = "approved" if body.decision == "approve" else "rejected"
    action.approved_by = user.id if body.decision == "approve" else None
    draft.status = "approved" if body.decision == "approve" else "rejected"
    ticket.status = "delivery_pending" if body.decision == "approve" else "rejected"
    ticket.revision += 1
    db.add(
        SupportAudit(
            workspace_id=ticket.workspace_id,
            ticket_id=ticket.id,
            actor_id=user.id,
            event="review.approved"
            if body.decision == "approve"
            else "review.rejected",
            details={
                "action_id": str(action.id),
                "revision": body.expected_revision,
                "idempotency_key": str(action.idempotency_key),
            },
        )
    )
    await db.commit()
    await db.refresh(ticket)
    if body.decision == "approve":
        from resolveai.jobs import enqueue_action

        await enqueue_action(ticket.workspace_id, action.id)
    return {
        "replayed": False,
        "decision": {
            "id": decision.id,
            "decision": decision.decision,
            "action": decision.action,
            "result": decision.result,
        },
        "ticket": await detail(db, ticket),
    }


async def process_action_once(
    *, workspace_id, action_id, factory=async_session_factory
):
    async with factory() as db:
        await set_tenant(db, workspace_id, for_write=True)
        action = await db.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.workspace_id == workspace_id,
                ActionRequest.id == action_id,
            )
            .with_for_update()
        )
        if action is None or action.status != "approved":
            return False
        ticket = await db.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.workspace_id == workspace_id,
                SupportTicket.id == action.ticket_id,
            )
            .with_for_update()
        )
        role = await db.scalar(
            select(WorkspaceMember.role)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == action.approved_by,
                User.is_active.is_(True),
            )
        )
        action.attempts += 1
        try:
            if role is None or (
                action.risk == "sensitive" and role not in ("owner", "admin")
            ):
                raise HTTPException(
                    403,
                    "The approver no longer has permission. A new authorized review is required.",
                )
            if (
                ticket is None
                or ticket.status != "delivery_pending"
                or ticket.revision != action.revision + 1
            ):
                raise HTTPException(
                    409,
                    "The approved ticket changed before delivery. Prepare and review a new draft.",
                )
            conversation = None
            if ticket.customer_conversation_id:
                conversation = await db.get(
                    CustomerConversation, ticket.customer_conversation_id
                )
                if conversation is None:
                    raise HTTPException(
                        409, "The customer conversation was removed before delivery."
                    )
            order = await validate_action(db, ticket, action.kind, action.arguments)
            result = {
                "action": action.kind,
                "mock": action.kind in ("refund", "replacement"),
                "idempotency_key": str(action.idempotency_key),
            }
            if order:
                order.status = (
                    "refunded" if action.kind == "refund" else "replacement_requested"
                )
                order.refundable = order.replaceable = False
                result.update(
                    order_reference=order.reference,
                    order_status=order.status,
                    amount_cents=order.amount_cents,
                    currency=order.currency,
                )
            if ticket.customer_conversation_id:
                db.add(
                    CustomerMessage(
                        workspace_id=workspace_id,
                        conversation_id=conversation.id,
                        delivery_id=action.id,
                        role="agent",
                        content=action.reply,
                        sources=action.arguments.get("sources", []),
                    )
                )
                conversation.status = (
                    "waiting_for_agent" if action.kind == "escalate" else "active"
                )
                result["delivery"] = "customer_conversation"
            else:
                result["delivery"] = "recorded_in_support_inbox"
            db.add(
                SupportMessage(
                    workspace_id=workspace_id,
                    ticket_id=ticket.id,
                    role="agent",
                    content=action.reply,
                    actor_id=action.approved_by,
                )
            )
            ticket.status = (
                "escalated"
                if action.kind == "escalate"
                else "replied"
                if ticket.customer_conversation_id
                else "executed"
            )
            action.status, action.result, action.error = "succeeded", result, None
            db.add(
                SupportAudit(
                    workspace_id=workspace_id,
                    ticket_id=ticket.id,
                    actor_id=action.approved_by,
                    event="action.succeeded",
                    details={"action_id": str(action.id), **result},
                )
            )
        except HTTPException as error:
            action.status, action.error = "failed", str(error.detail)[:500]
            action.result = {"delivery": "not_sent", "error": action.error}
            if ticket and ticket.status == "delivery_pending":
                ticket.status, ticket.last_error = "open", action.error
            db.add(
                SupportAudit(
                    workspace_id=workspace_id,
                    ticket_id=action.ticket_id,
                    actor_id=action.approved_by,
                    event="action.failed",
                    details={"action_id": str(action.id), "reason": action.error},
                )
            )
        if action.decision_id:
            decision = await db.get(SupportDecision, action.decision_id)
            if decision:
                decision.result = action.result
        await db.commit()
    return True
