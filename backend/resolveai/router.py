"""Authenticated ResolveAI API. Every object lookup is scoped to membership."""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    BusinessDocument,
    DemoOrder,
    KnowledgeAnswer,
    SupportAudit,
    SupportDecision,
    SupportFeedback,
    SupportMessage,
    SupportRun,
    SupportTicket,
    User,
    Workspace,
    WorkspaceMember,
)
from database.session import get_db
from rag import MAX_UPLOAD_BYTES, pinecone_is_configured, safe_filename
from resolveai.knowledge import retrieve
from resolveai.schemas import (
    Feedback,
    MemberCreate,
    OrderCreate,
    Question,
    Review,
    TicketCreate,
    WorkspaceCreate,
)
from resolveai.workflow import PROMPT_VERSION, QUEUES, SupportEngine


def fields(obj, *names):
    return {name: getattr(obj, name) for name in names}


def ticket_summary(ticket):
    return fields(
        ticket,
        "id",
        "subject",
        "customer_email",
        "order_reference",
        "status",
        "category",
        "priority",
        "queue",
        "revision",
        "created_at",
        "updated_at",
        "last_error",
    )


def document_view(document):
    return fields(
        document,
        "id",
        "filename",
        "status",
        "chunk_count",
        "attempts",
        "error",
        "created_at",
    )


def audit(db, workspace_id, actor_id, event, ticket_id=None, **details):
    db.add(
        SupportAudit(
            workspace_id=workspace_id,
            actor_id=actor_id,
            event=event,
            ticket_id=ticket_id,
            details=details,
        )
    )


async def member(db, workspace_id, user_id, admin=False):
    row = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Workspace not found.")
    if admin and row[1] != "admin":
        raise HTTPException(403, "Only workspace admins can perform this operation.")
    return row[0], row[1]


async def ticket_in_workspace(db, workspace_id, ticket_id, lock=False):
    query = select(SupportTicket).where(
        SupportTicket.workspace_id == workspace_id, SupportTicket.id == ticket_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    ticket = await db.scalar(query)
    if ticket is None:
        raise HTTPException(404, "Ticket not found.")
    return ticket


async def detail(db, ticket):
    result = {
        **ticket_summary(ticket),
        **fields(
            ticket, "description", "draft", "proposed_action", "sources", "tool_calls"
        ),
    }
    messages = (
        await db.scalars(
            select(SupportMessage)
            .where(
                SupportMessage.workspace_id == ticket.workspace_id,
                SupportMessage.ticket_id == ticket.id,
            )
            .order_by(SupportMessage.created_at, SupportMessage.id)
        )
    ).all()
    decisions = (
        await db.scalars(
            select(SupportDecision)
            .where(
                SupportDecision.workspace_id == ticket.workspace_id,
                SupportDecision.ticket_id == ticket.id,
            )
            .order_by(SupportDecision.created_at)
        )
    ).all()
    runs = (
        await db.scalars(
            select(SupportRun)
            .where(
                SupportRun.workspace_id == ticket.workspace_id,
                SupportRun.ticket_id == ticket.id,
            )
            .order_by(SupportRun.created_at.desc())
            .limit(10)
        )
    ).all()
    result["messages"] = [
        fields(m, "id", "role", "content", "created_at") for m in messages
    ]
    result["decisions"] = [
        fields(
            d,
            "id",
            "ticket_revision",
            "decision",
            "action",
            "reply",
            "result",
            "actor_id",
            "created_at",
        )
        for d in decisions
    ]
    result["runs"] = [
        fields(
            r,
            "id",
            "status",
            "mode",
            "model",
            "prompt_version",
            "tokens",
            "duration_ms",
            "error",
            "created_at",
        )
        for r in runs
    ]
    return result


async def reserve_run(db, workspace, ticket_id=None):
    # Serialize budget checks across processes, rather than an in-memory counter.
    await db.scalar(
        select(Workspace).where(Workspace.id == workspace.id).with_for_update()
    )
    recent = await db.scalar(
        select(func.count())
        .select_from(SupportRun)
        .where(
            SupportRun.workspace_id == workspace.id,
            SupportRun.created_at > datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    if recent >= 10:
        raise HTTPException(
            429,
            "This workspace has reached 10 workflow requests per minute. Please wait.",
        )
    run = SupportRun(
        workspace_id=workspace.id,
        ticket_id=ticket_id,
        mode=workspace.ai_mode,
        model="local-rules" if workspace.ai_mode == "local" else "gemini",
        prompt_version=PROMPT_VERSION,
    )
    db.add(run)
    await db.flush()
    return run


def build_router(current_user, csrf, generate, configured_model, map_error):
    router = APIRouter(prefix="/api/support", tags=["ResolveAI"])

    def engine(mode):
        return SupportEngine(mode, generate, configured_model, map_error)

    @router.get("/workspaces")
    async def list_workspaces(
        user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
    ):
        rows = (
            await db.execute(
                select(Workspace, WorkspaceMember.role)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(WorkspaceMember.user_id == user.id)
                .order_by(Workspace.created_at)
            )
        ).all()
        return [
            {**fields(w, "id", "name", "ai_mode", "created_at"), "role": role}
            for w, role in rows
        ]

    @router.post("/workspaces", status_code=201, dependencies=[Depends(csrf)])
    async def create_workspace(
        body: WorkspaceCreate,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        if body.ai_mode == "gemini" and (
            not os.getenv("GEMINI_API_KEY") or not pinecone_is_configured()
        ):
            raise HTTPException(
                503,
                "Gemini workspaces require GEMINI_API_KEY, PINECONE_API_KEY and PINECONE_INDEX_NAME. Local demo mode needs no provider keys.",
            )
        # Per-account limit is serialized by the user row.
        await db.scalar(select(User).where(User.id == user.id).with_for_update())
        count = await db.scalar(
            select(func.count())
            .select_from(Workspace)
            .where(Workspace.owner_id == user.id)
        )
        if count >= 10:
            raise HTTPException(
                409, "This account already owns the maximum of ten workspaces."
            )
        workspace = Workspace(name=body.name, owner_id=user.id, ai_mode=body.ai_mode)
        db.add(workspace)
        await db.flush()
        db.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin")
        )
        audit(db, workspace.id, user.id, "workspace.created", mode=body.ai_mode)
        await db.commit()
        await db.refresh(workspace)
        return {
            **fields(workspace, "id", "name", "ai_mode", "created_at"),
            "role": "admin",
        }

    @router.get("/workspaces/{workspace_id}/overview")
    async def overview(
        workspace_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        workspace, role = await member(db, workspace_id, user.id)
        tickets = (
            await db.scalars(
                select(SupportTicket)
                .where(SupportTicket.workspace_id == workspace_id)
                .order_by(SupportTicket.created_at.desc())
                .limit(100)
            )
        ).all()
        documents = (
            await db.scalars(
                select(BusinessDocument)
                .where(BusinessDocument.workspace_id == workspace_id)
                .order_by(BusinessDocument.created_at.desc())
            )
        ).all()
        orders = (
            await db.scalars(
                select(DemoOrder)
                .where(DemoOrder.workspace_id == workspace_id)
                .order_by(DemoOrder.reference)
                .limit(100)
            )
        ).all()
        events = (
            await db.scalars(
                select(SupportAudit)
                .where(SupportAudit.workspace_id == workspace_id)
                .order_by(SupportAudit.created_at.desc())
                .limit(100)
            )
        ).all()
        runs = (
            await db.scalars(
                select(SupportRun)
                .where(SupportRun.workspace_id == workspace_id)
                .order_by(SupportRun.created_at.desc())
                .limit(50)
            )
        ).all()
        counts = dict(
            (
                await db.execute(
                    select(SupportTicket.status, func.count())
                    .where(SupportTicket.workspace_id == workspace_id)
                    .group_by(SupportTicket.status)
                )
            ).all()
        )
        feedback = (
            await db.execute(
                select(SupportFeedback.vote, func.count())
                .where(SupportFeedback.workspace_id == workspace_id)
                .group_by(SupportFeedback.vote)
            )
        ).all()
        members = (
            await db.execute(
                select(User.email, WorkspaceMember.role)
                .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                .where(WorkspaceMember.workspace_id == workspace_id)
            )
        ).all()
        answers = (
            await db.scalars(
                select(KnowledgeAnswer)
                .where(KnowledgeAnswer.workspace_id == workspace_id)
                .order_by(KnowledgeAnswer.created_at.desc())
                .limit(20)
            )
        ).all()
        return {
            "workspace": {**fields(workspace, "id", "name", "ai_mode"), "role": role},
            "tickets": [ticket_summary(t) for t in tickets],
            "documents": [document_view(d) for d in documents],
            "orders": [
                fields(
                    o,
                    "id",
                    "reference",
                    "customer_email",
                    "item",
                    "status",
                    "amount_cents",
                    "currency",
                    "refundable",
                    "replaceable",
                )
                for o in orders
            ],
            "audit": [
                fields(
                    e, "id", "event", "ticket_id", "actor_id", "details", "created_at"
                )
                for e in events
            ],
            "members": (
                [{"email": email, "role": r} for email, r in members]
                if role == "admin"
                else []
            ),
            "answers": [
                fields(a, "id", "question", "answer", "sources", "mode", "created_at")
                for a in answers
            ],
            "metrics": {
                "tickets": sum(counts.values()),
                "statuses": counts,
                "recent_runs": len(runs),
                "failed_runs": sum(r.status == "failed" for r in runs),
                "recent_tokens": sum(r.tokens for r in runs),
                "average_duration_ms": round(
                    sum(r.duration_ms for r in runs) / max(1, len(runs))
                ),
                "feedback": {str(v): n for v, n in feedback},
            },
        }

    @router.post(
        "/workspaces/{workspace_id}/members",
        status_code=201,
        dependencies=[Depends(csrf)],
    )
    async def add_member(
        workspace_id: UUID,
        body: MemberCreate,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id, admin=True)
        target = await db.scalar(
            select(User).where(User.email == str(body.email).lower())
        )
        if target is None:
            raise HTTPException(
                404,
                "Ask this teammate to create an account first. No invitation email is sent.",
            )
        if await db.get(WorkspaceMember, (workspace_id, target.id)):
            raise HTTPException(409, "This user is already a member.")
        db.add(
            WorkspaceMember(
                workspace_id=workspace_id, user_id=target.id, role=body.role
            )
        )
        audit(
            db,
            workspace_id,
            user.id,
            "member.added",
            member_id=str(target.id),
            role=body.role,
        )
        try:
            await db.commit()
        except IntegrityError as error:
            await db.rollback()
            raise HTTPException(409, "This user is already a member.") from error
        return {"email": target.email, "role": body.role}

    @router.post(
        "/workspaces/{workspace_id}/documents",
        status_code=202,
        dependencies=[Depends(csrf)],
    )
    async def upload_document(
        workspace_id: UUID,
        file: UploadFile = File(...),
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        workspace, _ = await member(db, workspace_id, user.id, admin=True)
        filename = safe_filename(file.filename or "")
        if not filename.lower().endswith((".pdf", ".docx", ".txt", ".md")):
            raise HTTPException(422, "Upload PDF, DOCX, UTF-8 TXT or Markdown.")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(422, "Upload a nonempty file up to 8 MB.")
        await db.scalar(
            select(Workspace).where(Workspace.id == workspace_id).with_for_update()
        )
        count = await db.scalar(
            select(func.count())
            .select_from(BusinessDocument)
            .where(BusinessDocument.workspace_id == workspace_id)
        )
        if count >= 20:
            raise HTTPException(
                409,
                "A workspace can contain at most twenty documents. Remove an old document first.",
            )
        digest = hashlib.sha256(data).hexdigest()
        duplicate = await db.scalar(
            select(BusinessDocument).where(
                BusinessDocument.workspace_id == workspace_id,
                BusinessDocument.content_hash == digest,
            )
        )
        if duplicate:
            raise HTTPException(409, "This document is already in this workspace.")
        document = BusinessDocument(
            workspace_id=workspace_id,
            filename=filename,
            content_hash=digest,
            content=data,
        )
        db.add(document)
        await db.flush()
        audit(
            db, workspace_id, user.id, "document.queued", document_id=str(document.id)
        )
        await db.commit()
        await db.refresh(document)
        return document_view(document)

    @router.post(
        "/workspaces/{workspace_id}/documents/{document_id}/retry",
        dependencies=[Depends(csrf)],
    )
    async def retry_document(
        workspace_id: UUID,
        document_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id, admin=True)
        document = await db.scalar(
            select(BusinessDocument)
            .where(
                BusinessDocument.workspace_id == workspace_id,
                BusinessDocument.id == document_id,
            )
            .with_for_update()
        )
        if document is None:
            raise HTTPException(404, "Document not found.")
        if (
            document.status != "failed"
            or document.attempts >= 3
            or document.content is None
        ):
            raise HTTPException(
                409,
                "This job cannot be retried. Remove it and upload a corrected document.",
            )
        document.status, document.error, document.lease_id = "queued", None, None
        audit(
            db, workspace_id, user.id, "document.retried", document_id=str(document_id)
        )
        await db.commit()
        return document_view(document)

    @router.delete(
        "/workspaces/{workspace_id}/documents/{document_id}",
        status_code=202,
        dependencies=[Depends(csrf)],
    )
    async def remove_document(
        workspace_id: UUID,
        document_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id, admin=True)
        document = await db.scalar(
            select(BusinessDocument)
            .where(
                BusinessDocument.workspace_id == workspace_id,
                BusinessDocument.id == document_id,
            )
            .with_for_update()
        )
        if document is None:
            raise HTTPException(404, "Document not found.")
        document.status, document.lease_id, document.attempts = "deleting", None, 0
        audit(
            db,
            workspace_id,
            user.id,
            "document.deletion_requested",
            document_id=str(document_id),
        )
        await db.commit()
        return {"status": "deleting"}

    @router.post(
        "/workspaces/{workspace_id}/tickets",
        status_code=201,
        dependencies=[Depends(csrf)],
    )
    async def create_ticket(
        workspace_id: UUID,
        body: TicketCreate,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id)
        ticket = SupportTicket(
            workspace_id=workspace_id,
            created_by=user.id,
            **body.model_dump(mode="json")
        )
        ticket.customer_email = ticket.customer_email.lower()
        db.add(ticket)
        await db.flush()
        db.add(
            SupportMessage(
                workspace_id=workspace_id,
                ticket_id=ticket.id,
                role="customer",
                content=body.description,
                actor_id=user.id,
            )
        )
        audit(db, workspace_id, user.id, "ticket.created", ticket.id)
        await db.commit()
        await db.refresh(ticket)
        return await detail(db, ticket)

    @router.get("/workspaces/{workspace_id}/tickets/{ticket_id}")
    async def get_ticket(
        workspace_id: UUID,
        ticket_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id)
        return await detail(db, await ticket_in_workspace(db, workspace_id, ticket_id))

    @router.post(
        "/workspaces/{workspace_id}/tickets/{ticket_id}/draft",
        dependencies=[Depends(csrf)],
    )
    async def draft_ticket(
        workspace_id: UUID,
        ticket_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        workspace, _ = await member(db, workspace_id, user.id)
        ticket = await ticket_in_workspace(db, workspace_id, ticket_id, lock=True)
        if ticket.status == "executed":
            raise HTTPException(409, "This ticket has already been resolved.")
        if ticket.status == "processing":
            active = await db.scalar(
                select(SupportRun)
                .where(
                    SupportRun.ticket_id == ticket.id, SupportRun.status == "running"
                )
                .order_by(SupportRun.created_at.desc())
                .limit(1)
            )
            if active and active.created_at > datetime.now(timezone.utc) - timedelta(
                minutes=3
            ):
                raise HTTPException(409, "A workflow is already preparing this ticket.")
            if active:
                active.status, active.error = (
                    "failed",
                    "Previous worker interrupted; restarted by a user.",
                )
        run = await reserve_run(db, workspace, ticket.id)
        ticket.status, ticket.last_error = "processing", None
        ticket.revision += 1
        revision = ticket.revision
        initial = {
            "subject": ticket.subject,
            "message": ticket.description,
            "customer_email": ticket.customer_email,
            "order_reference": ticket.order_reference,
        }
        run_id = run.id
        await db.commit()
        service = engine(workspace.ai_mode)
        started = time.monotonic()
        failure = None
        try:
            async with asyncio.timeout(100):
                result = await service.graph(db, workspace_id, initial)
        except HTTPException as error:
            failure = error
        except TimeoutError:
            failure = HTTPException(
                503, "The workflow timed out. Retry or handle this ticket manually."
            )
        except Exception:
            failure = HTTPException(
                503,
                "The workflow could not access its tools or knowledge provider. Check configuration and retry.",
            )
        ticket = await ticket_in_workspace(db, workspace_id, ticket_id, lock=True)
        run = await db.get(SupportRun, run_id)
        if ticket.revision != revision or ticket.status != "processing":
            raise HTTPException(409, "This draft was superseded by another change.")
        run.duration_ms = round((time.monotonic() - started) * 1000)
        run.tokens, run.model = service.tokens, service.model
        run.completed_at = datetime.now(timezone.utc)
        if failure:
            run.status, run.error = "failed", str(failure.detail)[:500]
            ticket.status, ticket.last_error = "open", str(failure.detail)[:500]
            audit(
                db,
                workspace_id,
                user.id,
                "workflow.failed",
                ticket_id,
                run_id=str(run.id),
                status=failure.status_code,
            )
            await db.commit()
            raise failure
        run.status = "completed"
        ticket.category = result["triage"]["category"]
        ticket.priority = result["triage"]["priority"]
        ticket.queue = QUEUES[ticket.category]
        ticket.status = "awaiting_approval"
        ticket.draft, ticket.proposed_action = (
            result["draft"],
            result["proposed_action"],
        )
        ticket.sources, ticket.tool_calls = result["sources"], result["tool_calls"]
        audit(
            db,
            workspace_id,
            user.id,
            "workflow.completed",
            ticket_id,
            run_id=str(run.id),
            action=ticket.proposed_action["kind"],
            category=ticket.category,
            model=run.model,
            tokens=run.tokens,
            duration_ms=run.duration_ms,
        )
        await db.commit()
        await db.refresh(ticket)
        return await detail(db, ticket)

    @router.post(
        "/workspaces/{workspace_id}/tickets/{ticket_id}/review",
        dependencies=[Depends(csrf)],
    )
    async def review_ticket(
        workspace_id: UUID,
        ticket_id: UUID,
        body: Review,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id, admin=True)
        ticket = await ticket_in_workspace(db, workspace_id, ticket_id, lock=True)
        digest = hashlib.sha256(
            json.dumps(
                body.model_dump(mode="json", exclude={"request_id"}), sort_keys=True
            ).encode()
        ).hexdigest()
        previous = await db.scalar(
            select(SupportDecision).where(
                SupportDecision.workspace_id == workspace_id,
                SupportDecision.request_id == body.request_id,
            )
        )
        if previous is None:
            previous = await db.scalar(
                select(SupportDecision).where(
                    SupportDecision.workspace_id == workspace_id,
                    SupportDecision.ticket_id == ticket_id,
                    SupportDecision.ticket_revision == body.expected_revision,
                )
            )
        if previous:
            if previous.ticket_id != ticket_id or previous.request_hash != digest:
                raise HTTPException(
                    409,
                    "This review/idempotency key was already used with different parameters.",
                )
            return {
                "replayed": True,
                "decision": fields(previous, "id", "decision", "action", "result"),
                "ticket": await detail(db, ticket),
            }
        if (
            ticket.status != "awaiting_approval"
            or ticket.revision != body.expected_revision
        ):
            raise HTTPException(
                409, "The ticket changed. Reload it and review the latest draft."
            )
        kind = ticket.proposed_action.get("kind", "escalate")
        outcome = {"mock": True, "delivery": "not_sent", "action": kind}
        if body.decision == "approve":
            ids = [UUID(source["document_id"]) for source in ticket.sources]
            ready = (
                await db.scalars(
                    select(BusinessDocument.id)
                    .where(
                        BusinessDocument.id.in_(ids),
                        BusinessDocument.workspace_id == workspace_id,
                        BusinessDocument.status == "ready",
                    )
                    .order_by(BusinessDocument.id)
                    .with_for_update()
                )
            ).all()
            if set(ids) != set(ready) or (
                kind in ("refund", "replacement") and not ids
            ):
                raise HTTPException(
                    409,
                    "Policy evidence changed or was removed. Generate a new draft before approval.",
                )
            if kind in ("refund", "replacement"):
                order = await db.scalar(
                    select(DemoOrder)
                    .where(
                        DemoOrder.workspace_id == workspace_id,
                        DemoOrder.id == UUID(ticket.proposed_action["order_id"]),
                        DemoOrder.customer_email == ticket.customer_email,
                    )
                    .with_for_update()
                )
                allowed = (
                    order
                    and order.status in ("delivered", "shipped")
                    and (order.refundable if kind == "refund" else order.replaceable)
                )
                if (
                    not allowed
                    or order.amount_cents != ticket.proposed_action["amount_cents"]
                ):
                    raise HTTPException(
                        409,
                        "The order is no longer eligible. Generate a new draft or escalate.",
                    )
                order.status = (
                    "refunded" if kind == "refund" else "replacement_requested"
                )
                order.refundable = order.replaceable = False
                outcome.update(
                    {
                        "order_reference": order.reference,
                        "order_status": order.status,
                        "amount_cents": order.amount_cents,
                        "currency": order.currency,
                    }
                )
            ticket.status = "escalated" if kind == "escalate" else "executed"
            outcome["delivery"] = "recorded_in_demo_inbox"
            db.add(
                SupportMessage(
                    workspace_id=workspace_id,
                    ticket_id=ticket_id,
                    role="agent",
                    content=body.reply,
                    actor_id=user.id,
                )
            )
        else:
            ticket.status = "rejected"
        decision = SupportDecision(
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            ticket_revision=ticket.revision,
            request_id=body.request_id,
            request_hash=digest,
            decision=body.decision,
            action=kind,
            reply=body.reply,
            result=outcome,
            actor_id=user.id,
        )
        db.add(decision)
        ticket.revision += 1
        audit(
            db,
            workspace_id,
            user.id,
            "review.approved" if body.decision == "approve" else "review.rejected",
            ticket_id,
            action=kind,
            revision=body.expected_revision,
        )
        if body.decision == "approve":
            audit(
                db, workspace_id, user.id, "mock_action.executed", ticket_id, **outcome
            )
        # Order mutation, decision, simulated reply and audit are one DB transaction.
        await db.commit()
        await db.refresh(decision)
        await db.refresh(ticket)
        return {
            "replayed": False,
            "decision": fields(decision, "id", "decision", "action", "result"),
            "ticket": await detail(db, ticket),
        }

    @router.post(
        "/workspaces/{workspace_id}/tickets/{ticket_id}/feedback",
        dependencies=[Depends(csrf)],
    )
    async def feedback(
        workspace_id: UUID,
        ticket_id: UUID,
        body: Feedback,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id)
        await ticket_in_workspace(db, workspace_id, ticket_id)
        await db.execute(
            insert(SupportFeedback)
            .values(
                workspace_id=workspace_id,
                ticket_id=ticket_id,
                user_id=user.id,
                **body.model_dump()
            )
            .on_conflict_do_update(
                constraint="support_feedback_user", set_=body.model_dump()
            )
        )
        await db.commit()
        return {"saved": True}

    @router.post("/workspaces/{workspace_id}/ask", dependencies=[Depends(csrf)])
    async def ask_knowledge(
        workspace_id: UUID,
        body: Question,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        workspace, _ = await member(db, workspace_id, user.id)
        run = await reserve_run(db, workspace)
        await db.commit()
        service = engine(workspace.ai_mode)
        started = time.monotonic()
        try:
            async with asyncio.timeout(80):
                sources = await retrieve(
                    db, workspace_id, body.question, workspace.ai_mode
                )
                answer = await service.answer(body.question, sources)
        except Exception as error:
            run.status, run.error = (
                "failed",
                (
                    str(error.detail)[:500]
                    if isinstance(error, HTTPException)
                    else "Knowledge request failed. Check provider configuration and retry."
                ),
            )
            run.model, run.tokens = service.model, service.tokens
            run.duration_ms = round((time.monotonic() - started) * 1000)
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(503, run.error) from error
        result = KnowledgeAnswer(
            workspace_id=workspace_id,
            user_id=user.id,
            question=body.question,
            answer=answer,
            sources=sources,
            mode=workspace.ai_mode,
        )
        db.add(result)
        run.status, run.model, run.tokens = "completed", service.model, service.tokens
        run.duration_ms = round((time.monotonic() - started) * 1000)
        run.completed_at = datetime.now(timezone.utc)
        audit(
            db,
            workspace_id,
            user.id,
            "knowledge.answered",
            source_count=len(sources),
            mode=workspace.ai_mode,
        )
        await db.commit()
        await db.refresh(result)
        return fields(
            result, "id", "question", "answer", "sources", "mode", "created_at"
        )

    @router.post(
        "/workspaces/{workspace_id}/orders",
        status_code=201,
        dependencies=[Depends(csrf)],
    )
    async def create_order(
        workspace_id: UUID,
        body: OrderCreate,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id, admin=True)
        order = DemoOrder(workspace_id=workspace_id, **body.model_dump(mode="json"))
        order.customer_email = order.customer_email.lower()
        db.add(order)
        audit(db, workspace_id, user.id, "mock_order.created", reference=body.reference)
        try:
            await db.commit()
        except IntegrityError as error:
            await db.rollback()
            raise HTTPException(
                409, "An order with this reference already exists in the workspace."
            ) from error
        await db.refresh(order)
        return fields(order, "id", "reference", "status")

    @router.post("/workspaces/{workspace_id}/demo", dependencies=[Depends(csrf)])
    async def seed_demo(
        workspace_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        workspace, _ = await member(db, workspace_id, user.id, admin=True)
        if workspace.ai_mode != "local":
            raise HTTPException(
                409, "Create a Local demo workspace to use these sample fixtures."
            )
        await db.scalar(
            select(Workspace).where(Workspace.id == workspace_id).with_for_update()
        )
        policies = {
            "demo-shipping.md": "Shipping policy\nOrders normally ship within two business days. Shipped orders usually arrive in three to five business days. Support can check an order's recorded status. Delivery estimates are not guarantees.",
            "demo-returns.md": "Returns and replacement policy\nRefunds may be proposed for delivered orders marked refundable. Damaged items may be proposed for replacement when the order is marked replaceable. An admin must review policy and current order eligibility before a refund or replacement is processed. No real payment is issued by this demo.",
        }
        for filename, text in policies.items():
            if not await db.scalar(
                select(BusinessDocument.id).where(
                    BusinessDocument.workspace_id == workspace_id,
                    BusinessDocument.filename == filename,
                )
            ):
                db.add(
                    BusinessDocument(
                        workspace_id=workspace_id,
                        filename=filename,
                        content=text.encode(),
                        content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    )
                )
        fixtures = [
            (
                "ORD-1001",
                "alex@example.com",
                "Wireless keyboard",
                "shipped",
                7900,
                False,
                False,
                "Where is my order?",
                "Where is my order ORD-1001? Please check the shipping status.",
            ),
            (
                "ORD-1002",
                "maya@example.com",
                "Desk lamp",
                "delivered",
                12900,
                True,
                True,
                "My item arrived damaged",
                "My desk lamp from ORD-1002 arrived broken. Can I get a replacement?",
            ),
            (
                "ORD-1003",
                "nora@example.com",
                "Laptop sleeve",
                "delivered",
                4900,
                True,
                False,
                "Request a refund",
                "Please refund my delivered order ORD-1003 under the returns policy.",
            ),
        ]
        for (
            ref,
            email,
            item,
            status,
            amount,
            refund,
            replacement,
            subject,
            description,
        ) in fixtures:
            if not await db.scalar(
                select(DemoOrder.id).where(
                    DemoOrder.workspace_id == workspace_id, DemoOrder.reference == ref
                )
            ):
                db.add(
                    DemoOrder(
                        workspace_id=workspace_id,
                        reference=ref,
                        customer_email=email,
                        item=item,
                        status=status,
                        amount_cents=amount,
                        currency="USD",
                        refundable=refund,
                        replaceable=replacement,
                    )
                )
            if not await db.scalar(
                select(SupportTicket.id).where(
                    SupportTicket.workspace_id == workspace_id,
                    SupportTicket.subject == subject,
                )
            ):
                ticket = SupportTicket(
                    workspace_id=workspace_id,
                    subject=subject,
                    description=description,
                    customer_email=email,
                    order_reference=ref,
                    created_by=user.id,
                )
                db.add(ticket)
                await db.flush()
                db.add(
                    SupportMessage(
                        workspace_id=workspace_id,
                        ticket_id=ticket.id,
                        role="customer",
                        content=description,
                        actor_id=user.id,
                    )
                )
        audit(db, workspace_id, user.id, "demo.loaded")
        await db.commit()
        return {
            "loaded": True,
            "message": "Sample orders and tickets loaded. Documents are queued for the worker.",
        }

    return router
