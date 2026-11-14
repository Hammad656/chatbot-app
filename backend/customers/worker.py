"""Durable customer turns: bounded routing, grounded answers and governed handoff."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool
from sqlalchemy import select, text

from ai.gemini import (
    _generate_gemini_content,
    _gemini_http_error,
    configured_gemini_model,
)
from ai.prompts import prompt
from customers.schemas import ChatRoute
from database.models import (
    ActionRequest,
    CustomerContact,
    CustomerConversation,
    CustomerMessage,
    CustomerSession,
    CustomerTurn,
    SupportAudit,
    SupportMessage,
    SupportTicket,
    Workspace,
)
from database.session import async_session_factory
from database.tenant import set_tenant
from resolveai.knowledge import retrieve
from resolveai.workflow import SupportEngine, local_triage


def local_route(content):
    triage = local_triage("Customer chat", content, None)
    lowered = content.lower()
    escalate = any(
        term in lowered
        for term in (
            "human",
            "agent",
            "complaint",
            "account",
            "fraud",
            "security",
            "refund",
            "replacement",
            "damaged",
            "charged",
        )
    )
    explicit_order = triage.order_reference or any(
        phrase in lowered
        for phrase in (
            "where is my order",
            "order status",
            "track my order",
            "my order",
            "help with an order",
        )
    )
    intent = (
        "escalation"
        if escalate
        else "order"
        if triage.category == "shipping" and explicit_order
        else "knowledge"
    )
    return ChatRoute(intent=intent, confidence=0.95, **triage.model_dump())


def ticket_tool(db, workspace, contact, conversation, turn, route):
    @tool("create_ticket")
    async def create_ticket() -> dict:
        """Open or update a support request for this authenticated conversation only. Never execute order actions."""
        from resolveai.router import reserve_run

        ticket = await db.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.workspace_id == workspace.id,
                SupportTicket.customer_conversation_id == conversation.id,
                SupportTicket.status != "delivery_pending",
                SupportTicket.updated_at
                > datetime.now(timezone.utc) - timedelta(days=7),
            )
            .order_by(SupportTicket.updated_at.desc())
            .limit(1)
            .with_for_update()
        )
        if ticket is None:
            ticket = SupportTicket(
                workspace_id=workspace.id,
                customer_conversation_id=conversation.id,
                subject=turn.content[:155],
                description=turn.content,
                customer_email=contact.email
                or f"guest-{contact.id.hex}@customers.invalid",
            )
            db.add(ticket)
            await db.flush()
        else:
            ticket.description = (
                ticket.description + "\n\nCustomer follow-up: " + turn.content
            )[-12000:]
            for action in (
                await db.scalars(
                    select(ActionRequest).where(
                        ActionRequest.ticket_id == ticket.id,
                        ActionRequest.status == "proposed",
                    )
                )
            ).all():
                action.status = "superseded"
        ticket.order_reference = route.order_reference if contact.email else None
        ticket.status, ticket.last_error = "processing", None
        ticket.revision += 1
        run = await reserve_run(db, workspace, ticket.id)
        run.status = "queued"
        run.job_context = {
            "actor_id": None,
            "customer_conversation_id": str(conversation.id),
            "revision": ticket.revision,
            "tries": 0,
            "request_id": str(turn.request_id),
            "triage": {
                key: value
                for key, value in route.model_dump().items()
                if key not in ("intent", "confidence")
            },
        }
        db.add(
            SupportMessage(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                role="customer",
                content=turn.content,
            )
        )
        db.add(
            SupportAudit(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                event="tool.create_ticket",
                details={
                    "turn_id": str(turn.id),
                    "conversation_id": str(conversation.id),
                    "run_id": str(run.id),
                    "governed_by": "customer_handoff",
                },
            )
        )
        return {"ticket_id": str(ticket.id), "run_id": str(run.id)}

    return create_ticket


async def _process_customer_turn_once(
    *, workspace_id, turn_id, factory=async_session_factory
):
    from resolveai.router import reserve_run

    async with factory() as db:
        await set_tenant(db, workspace_id, for_write=True)
        turn = await db.scalar(
            select(CustomerTurn)
            .where(
                CustomerTurn.id == turn_id, CustomerTurn.workspace_id == workspace_id
            )
            .with_for_update()
        )
        if turn is None or turn.status != "queued":
            return False
        cs = await db.get(CustomerSession, turn.session_id)
        active = cs and await db.scalar(
            text("SELECT resolveai_customer_session(:w,:s,:c)"),
            {"w": workspace_id, "s": cs.id, "c": turn.conversation_id},
        )
        if not active or turn.attempts >= 3:
            turn.status, turn.error = (
                "failed",
                "Customer session ended or retry budget exhausted.",
            )
            await db.commit()
            return False
        turn.status, turn.attempts = "running", turn.attempts + 1
        attempt, content, contact_id = turn.attempts, turn.content, cs.contact_id
        verified = cs.access_link_id is not None
        workspace = await db.get(Workspace, workspace_id)
        run = await reserve_run(db, workspace)
        run.job_context = {"kind": "customer_turn", "turn_id": str(turn.id)}
        run_id = run.id
        mode, settings = workspace.ai_mode, workspace.settings
        await db.commit()
    engine = SupportEngine(
        mode,
        _generate_gemini_content,
        configured_gemini_model,
        _gemini_http_error,
        settings=settings,
    )
    started, failure, answer, sources, route = time.monotonic(), None, None, [], None
    try:
        async with asyncio.timeout(30):
            route = (
                local_route(content)
                if mode == "local"
                else await engine.structured(
                    ChatRoute,
                    prompt("route_v1"),
                    {"message": content, "customer_verified": verified},
                )
            )
            if route.confidence < 0.65:
                route.intent = "escalation"
            if route.intent == "order" and not verified:
                answer = "For your privacy, order details require your private customer access link. Open the link supplied by the company, then ask again with your order reference. I can still help with general company policies here."
            elif route.intent == "order" and not route.order_reference:
                answer = "Please share your order reference, for example ORD-1001. Your support team can then check the matching order and review a reply for you."
            elif route.intent == "knowledge":
                async with factory() as read:
                    await set_tenant(read, workspace_id)
                    sources = await retrieve(read, workspace_id, content, mode)
                    history = list(
                        (
                            await read.scalars(
                                select(CustomerMessage)
                                .where(
                                    CustomerMessage.conversation_id
                                    == turn.conversation_id,
                                    CustomerMessage.workspace_id == workspace_id,
                                )
                                .order_by(CustomerMessage.created_at.desc())
                                .limit(6)
                            )
                        ).all()
                    )
                    context = [
                        {"question": message.content[:400], "answer": ""}
                        for message in reversed(history)
                        if message.role == "customer"
                    ]
                    if sources:
                        answer = await engine.answer(content, sources, context)
                    else:
                        route.intent = "escalation"
    except Exception:
        failure = "Support routing or knowledge retrieval is temporarily unavailable."
    queued = None
    async with factory() as db:
        await set_tenant(db, workspace_id, for_write=True)
        turn = await db.scalar(
            select(CustomerTurn)
            .where(
                CustomerTurn.id == turn_id, CustomerTurn.workspace_id == workspace_id
            )
            .with_for_update()
        )
        if turn is None or turn.status != "running" or turn.attempts != attempt:
            return False
        from database.models import SupportRun

        run = await db.get(SupportRun, run_id)
        cs = await db.get(CustomerSession, turn.session_id)
        active = cs and await db.scalar(
            text("SELECT resolveai_customer_session(:w,:s,:c)"),
            {"w": workspace_id, "s": cs.id, "c": turn.conversation_id},
        )
        if not active:
            failure = "Customer session ended while processing."
        if failure:
            turn.status = "queued" if active and attempt < 3 else "failed"
            turn.error = failure
        else:
            conversation = await db.get(CustomerConversation, turn.conversation_id)
            if answer is None:
                workspace = await db.get(Workspace, workspace_id)
                contact = await db.get(CustomerContact, contact_id)
                queued = await ticket_tool(
                    db, workspace, contact, conversation, turn, route
                ).ainvoke({})
                from uuid import UUID

                turn.ticket_id = UUID(queued["ticket_id"])
                conversation.status = "waiting_for_agent"
                answer = f"I've opened support request {str(turn.ticket_id)[:8].upper()}. A support agent will review your request and reply in this conversation. No refund, replacement or other order change has been performed."
                turn.status = "awaiting_review"
            else:
                turn.status = "completed"
            conversation.title = content[:100]
            db.add(
                CustomerMessage(
                    workspace_id=workspace_id,
                    conversation_id=turn.conversation_id,
                    turn_id=turn.id,
                    role="assistant",
                    content=answer,
                    sources=sources,
                )
            )
        if run:
            run.status, run.error = (
                ("failed", failure) if failure else ("completed", None)
            )
            run.tokens, run.model = engine.tokens, engine.model
            run.duration_ms = round((time.monotonic() - started) * 1000)
            run.completed_at = datetime.now(timezone.utc)
        await db.commit()
    if queued:
        from resolveai.jobs import enqueue_draft

        await enqueue_draft(workspace_id, queued["run_id"])
    return True


async def process_customer_turn_once(
    *, workspace_id, turn_id, factory=async_session_factory
):
    try:
        return await _process_customer_turn_once(
            workspace_id=workspace_id, turn_id=turn_id, factory=factory
        )
    except Exception as error:
        # An exhausted workflow budget or an interrupted final handoff must not
        # leave a customer polling a permanently 'running' turn.
        import logging

        logging.getLogger(__name__).warning(
            "Customer turn could not finish (%s), turn_id=%s",
            type(error).__name__,
            turn_id,
        )
        async with factory() as db:
            await set_tenant(db, workspace_id, for_write=True)
            turn = await db.scalar(
                select(CustomerTurn)
                .where(
                    CustomerTurn.id == turn_id,
                    CustomerTurn.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            if turn is None or turn.status in (
                "completed",
                "awaiting_review",
                "failed",
            ):
                return False
            turn.status, turn.error = (
                "failed",
                "Support is temporarily busy. Please retry shortly or contact the company directly.",
            )
            from database.models import SupportRun

            for run in (
                await db.scalars(
                    select(SupportRun).where(
                        SupportRun.workspace_id == workspace_id,
                        SupportRun.status == "running",
                        SupportRun.job_context["turn_id"].as_string() == str(turn_id),
                    )
                )
            ).all():
                run.status, run.error, run.completed_at = (
                    "failed",
                    turn.error,
                    datetime.now(timezone.utc),
                )
            existing = await db.scalar(
                select(CustomerMessage.id).where(
                    CustomerMessage.turn_id == turn.id,
                    CustomerMessage.role == "assistant",
                )
            )
            if existing is None:
                db.add(
                    CustomerMessage(
                        workspace_id=workspace_id,
                        conversation_id=turn.conversation_id,
                        turn_id=turn.id,
                        role="assistant",
                        content=turn.error,
                    )
                )
            await db.commit()
        return True
