import asyncio
import json
import secrets
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auth.security import secure_cookie, token_hash
from customers.schemas import Feedback, Message, OpenSession
from customers.security import (
    CustomerPrincipal,
    encode_customer,
    get_customer,
    validate_origin,
)
from database.models import (
    CustomerContact,
    CustomerConversation,
    CustomerFeedback,
    CustomerMessage,
    CustomerSession,
    CustomerTurn,
)
from database.session import get_db
from database.tenant import set_customer
from resolveai.limits import rate_limit

router = APIRouter(prefix="/api/v1/widget", tags=["Customer support"])


def cookie_name(key):
    return "resolveai_customer_" + key[:16]


async def widget_config(db, key):
    if len(key) > 64:
        raise HTTPException(404, "Customer support is unavailable.")
    row = (
        (
            await db.execute(
                text("SELECT * FROM resolveai_widget_config(:key)"), {"key": key}
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(
            404, "Customer support is unavailable or has been disabled."
        )
    return row


@router.get("/config/{workspace_key}")
async def config(workspace_key: str, db: AsyncSession = Depends(get_db)):
    row = await widget_config(db, workspace_key)
    return {"name": row["name"], "allowed_origins": row["origins"], "enabled": True}


@router.post("/session")
async def open_session(
    body: OpenSession,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    await rate_limit(
        "widget_session_ip",
        request.client.host if request.client else "unknown",
        20,
        60,
    )
    row = await widget_config(db, body.workspace_key)
    await rate_limit("widget_session_workspace", str(row["id"]), 100, 60)
    site = validate_origin(request, row["origins"], body.site_origin)
    old = request.cookies.get(cookie_name(body.workspace_key))
    raw = secrets.token_urlsafe(48)
    try:
        record = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM resolveai_open_customer_session(:key,:access,:cookie,:fresh,:origin)"
                    ),
                    {
                        "key": body.workspace_key,
                        "access": token_hash(body.access_token)
                        if body.access_token
                        else None,
                        "cookie": token_hash(old) if old else None,
                        "fresh": token_hash(raw),
                        "origin": site,
                    },
                )
            )
            .mappings()
            .one()
        )
    except DBAPIError as error:
        await db.rollback()
        if getattr(error.orig, "sqlstate", None) == "P0001":
            raise HTTPException(
                401, "Your customer access link is invalid, expired or revoked."
            ) from None
        raise
    principal = CustomerPrincipal(
        record["sid"],
        record["wid"],
        record["cid"],
        record["contact"],
        record["verified"],
    )
    await set_customer(
        db, principal.workspace_id, principal.session_id, principal.conversation_id
    )
    contact = await db.get(CustomerContact, principal.contact_id)
    current = await db.get(CustomerSession, principal.session_id)
    cookie = old if old and current.cookie_hash == token_hash(old) else raw
    response.set_cookie(
        cookie_name(body.workspace_key),
        cookie,
        max_age=max(
            1, int((record["expires"] - datetime.now(timezone.utc)).total_seconds())
        ),
        httponly=True,
        secure=secure_cookie(),
        samesite="lax",
        path="/api/v1/widget",
    )
    await db.commit()
    return {
        "access_token": encode_customer(principal, record["expires"]),
        "conversation_id": principal.conversation_id,
        "expires_at": record["expires"],
        "role": "customer",
        "verified": principal.verified,
        "name": contact.name,
        "email": contact.email if principal.verified else None,
        "company_name": row["name"],
    }


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    principal: CustomerPrincipal = Depends(get_customer),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(CustomerSession, principal.session_id)
    await db.execute(
        text("SELECT resolveai_close_customer_session(:sid)"),
        {"sid": principal.session_id},
    )
    await db.commit()
    for name in request.cookies:
        if (
            name.startswith("resolveai_customer_")
            and token_hash(request.cookies[name]) == record.cookie_hash
        ):
            response.delete_cookie(
                name,
                path="/api/v1/widget",
                secure=secure_cookie(),
                httponly=True,
                samesite="lax",
            )
    return Response(status_code=204, headers=response.headers)


def message_view(message):
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "sources": message.sources,
        "created_at": message.created_at,
    }


@router.get("/conversations/{conversation_id}")
async def conversation(
    conversation_id: UUID,
    principal: CustomerPrincipal = Depends(get_customer),
    db: AsyncSession = Depends(get_db),
):
    if conversation_id != principal.conversation_id:
        raise HTTPException(404, "Conversation not found.")
    record = await db.get(CustomerConversation, conversation_id)
    messages = list(
        (
            await db.scalars(
                select(CustomerMessage)
                .where(
                    CustomerMessage.workspace_id == principal.workspace_id,
                    CustomerMessage.conversation_id == conversation_id,
                )
                .order_by(CustomerMessage.created_at.desc(), CustomerMessage.id.desc())
                .limit(100)
            )
        ).all()
    )
    pending = await db.scalar(
        select(CustomerTurn)
        .where(
            CustomerTurn.conversation_id == conversation_id,
            CustomerTurn.status.in_(["queued", "running"]),
        )
        .order_by(CustomerTurn.created_at.desc())
        .limit(1)
    )
    return {
        "id": record.id,
        "status": record.status,
        "messages": [message_view(m) for m in reversed(messages)],
        "processing": pending is not None,
    }


def event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/messages")
async def send_message(
    body: Message,
    request: Request,
    principal: CustomerPrincipal = Depends(get_customer),
    db: AsyncSession = Depends(get_db),
):
    await rate_limit(
        "widget_message_ip",
        request.client.host if request.client else "unknown",
        60,
        60,
    )
    await rate_limit("widget_messages", str(principal.session_id), 12, 60)
    await rate_limit("widget_workspace_messages", str(principal.workspace_id), 120, 60)
    existing = await db.scalar(
        select(CustomerTurn).where(
            CustomerTurn.conversation_id == principal.conversation_id,
            CustomerTurn.request_id == body.request_id,
        )
    )
    if existing and existing.content != body.content:
        raise HTTPException(
            409, "This message identifier was already used for different content."
        )
    if existing is None:
        active = await db.scalar(
            select(CustomerTurn.id).where(
                CustomerTurn.conversation_id == principal.conversation_id,
                CustomerTurn.status.in_(["queued", "running"]),
            )
        )
        if active:
            raise HTTPException(409, "Please wait for your previous message to finish.")
        existing = CustomerTurn(
            workspace_id=principal.workspace_id,
            conversation_id=principal.conversation_id,
            session_id=principal.session_id,
            request_id=body.request_id,
            content=body.content,
        )
        db.add(existing)
        await db.flush()
        db.add(
            CustomerMessage(
                workspace_id=principal.workspace_id,
                conversation_id=principal.conversation_id,
                turn_id=existing.id,
                role="customer",
                content=body.content,
            )
        )
        await db.commit()
    turn_id = existing.id
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    from resolveai.jobs import enqueue_customer_turn

    await enqueue_customer_turn(principal.workspace_id, turn_id)
    # Release this dependency's transaction before holding an SSE connection open.
    await db.commit()

    async def stream():
        yield event("ack", {"turn_id": turn_id})
        deadline = time.monotonic() + 65
        last_status = None
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            async with factory() as session:
                await set_customer(
                    session,
                    principal.workspace_id,
                    principal.session_id,
                    principal.conversation_id,
                )
                turn = await session.get(CustomerTurn, turn_id)
                if turn is None:
                    yield event(
                        "error",
                        {"message": "Your session ended. Reopen customer support."},
                    )
                    return
                status, ticket_id = turn.status, turn.ticket_id
                message = await session.scalar(
                    select(CustomerMessage).where(
                        CustomerMessage.turn_id == turn_id,
                        CustomerMessage.role.in_(["assistant", "system"]),
                    )
                )
                payload = message_view(message) if message else None
            if last_status != status:
                yield event("status", {"status": status})
                last_status = status
            if status in ("completed", "awaiting_review", "failed"):
                if status == "failed":
                    yield event(
                        "error",
                        {
                            "message": "Support could not complete this request. Please retry or contact the company."
                        },
                    )
                if payload:
                    # Only completed, validated content is streamed; no unreviewed order draft leaks.
                    for offset in range(0, len(payload["content"]), 48):
                        yield event(
                            "delta", {"text": payload["content"][offset : offset + 48]}
                        )
                    yield event("message", payload)
                if ticket_id:
                    yield event(
                        "ticket", {"ticket_id": ticket_id, "status": "awaiting_review"}
                    )
                yield event("done", {"status": status})
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)
        yield event(
            "done",
            {
                "status": "processing",
                "message": "Your message is still being processed. The conversation will update automatically.",
            },
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/feedback")
async def feedback(
    body: Feedback,
    principal: CustomerPrincipal = Depends(get_customer),
    db: AsyncSession = Depends(get_db),
):
    message = await db.scalar(
        select(CustomerMessage).where(
            CustomerMessage.id == body.message_id,
            CustomerMessage.workspace_id == principal.workspace_id,
            CustomerMessage.conversation_id == principal.conversation_id,
            CustomerMessage.role.in_(["assistant", "agent"]),
        )
    )
    if message is None:
        raise HTTPException(404, "Answer not found.")
    await db.execute(
        insert(CustomerFeedback)
        .values(
            workspace_id=principal.workspace_id,
            conversation_id=principal.conversation_id,
            contact_id=principal.contact_id,
            message_id=message.id,
            vote=body.vote,
        )
        .on_conflict_do_update(
            constraint="customer_feedback_once", set_={"vote": body.vote}
        )
    )
    await db.commit()
    return {"saved": True}
