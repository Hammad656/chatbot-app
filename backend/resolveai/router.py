"""Authenticated ResolveAI API. Every object lookup is scoped to membership."""

import asyncio
import hashlib
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from database.models import (
    BusinessChunk,
    BusinessDocument,
    CustomerConversation,
    CustomerFeedback,
    CustomerMessage,
    CustomerTurn,
    ActionRequest,
    DemoOrder,
    KnowledgeAnswer,
    KnowledgeThread,
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
from database.tenant import set_tenant
from auth.dependencies import Principal
from workspaces.service import create as create_workspace_service
from resolveai.documents import MAX_UPLOAD_BYTES, safe_filename
from resolveai.jobs import enqueue_document, enqueue_draft
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
from resolveai.storage import save_upload
from resolveai.workflow import PROMPT_VERSION, SupportEngine


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
        "assigned_to",
        "customer_conversation_id",
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
    if str(workspace_id) != db.info.get("active_workspace_id"):
        raise HTTPException(
            404,
            "Workspace not found. Select it through the workspace switch endpoint first.",
        )
    row = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Workspace not found.")
    if admin and row[1] not in ("owner", "admin"):
        raise HTTPException(
            403, "Only workspace owners and admins can perform this operation."
        )
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
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": workspace.id.int & ((1 << 63) - 1)},
    )
    if (
        await db.scalar(select(Workspace.id).where(Workspace.id == workspace.id))
        is None
    ):
        raise HTTPException(401, "Workspace access changed. Refresh your session.")
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
    router = APIRouter(prefix="/api/v1", tags=["ResolveAI"])

    def engine(mode, settings=None):
        return SupportEngine(
            mode, generate, configured_model, map_error, settings=settings
        )

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
        principal = Principal(
            user,
            UUID(db.info["session_id"]),
            (
                UUID(db.info["active_workspace_id"])
                if db.info.get("active_workspace_id")
                else None
            ),
            db.info.get("principal_role"),
        )
        return await create_workspace_service(db, principal, body)

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
        customer_counts = dict(
            (
                await db.execute(
                    select(CustomerTurn.status, func.count())
                    .where(CustomerTurn.workspace_id == workspace_id)
                    .group_by(CustomerTurn.status)
                )
            ).all()
        )
        customer_feedback = dict(
            (
                await db.execute(
                    select(CustomerFeedback.vote, func.count())
                    .where(CustomerFeedback.workspace_id == workspace_id)
                    .group_by(CustomerFeedback.vote)
                )
            ).all()
        )
        conversation_count = await db.scalar(
            select(func.count())
            .select_from(CustomerConversation)
            .where(CustomerConversation.workspace_id == workspace_id)
        )
        delivered_replies = await db.scalar(
            select(func.count())
            .select_from(CustomerMessage)
            .where(
                CustomerMessage.workspace_id == workspace_id,
                CustomerMessage.role == "agent",
            )
        )
        queued_actions = await db.scalar(
            select(func.count())
            .select_from(ActionRequest)
            .where(
                ActionRequest.workspace_id == workspace_id,
                ActionRequest.status == "approved",
            )
        )
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
        threads = (
            await db.scalars(
                select(KnowledgeThread)
                .where(KnowledgeThread.workspace_id == workspace_id)
                .order_by(KnowledgeThread.created_at.desc())
                .limit(50)
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
                if role in ("owner", "admin")
            ],
            "audit": [
                fields(
                    e, "id", "event", "ticket_id", "actor_id", "details", "created_at"
                )
                for e in events
                if role in ("owner", "admin")
            ],
            "members": (
                [{"email": email, "role": r} for email, r in members]
                if role in ("owner", "admin")
                else []
            ),
            "answers": [
                fields(
                    a,
                    "id",
                    "thread_id",
                    "question",
                    "answer",
                    "sources",
                    "mode",
                    "created_at",
                )
                for a in answers
            ],
            "threads": [fields(t, "id", "title", "created_at") for t in threads],
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
                "customer_conversations": conversation_count,
                "self_service_answers": customer_counts.get("completed", 0),
                "human_handoffs": customer_counts.get("awaiting_review", 0),
                "delivered_replies": delivered_replies,
                "queued_actions": queued_actions,
                "customer_feedback": {str(v): n for v, n in customer_feedback.items()},
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
            raise HTTPException(422, "Upload a nonempty file up to 20 MB.")
        if filename.lower().endswith(".pdf") and not data.startswith(b"%PDF-"):
            raise HTTPException(422, "The file does not contain a PDF signature.")
        if filename.lower().endswith(".docx") and not data.startswith(b"PK"):
            raise HTTPException(422, "The file does not contain a DOCX signature.")
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
            storage_key=await run_in_threadpool(save_upload, data),
        )
        db.add(document)
        await db.flush()
        audit(
            db, workspace_id, user.id, "document.queued", document_id=str(document.id)
        )
        await db.commit()
        await db.refresh(document)
        await enqueue_document(workspace_id, document.id)
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
            or (document.content is None and document.storage_key is None)
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
        await enqueue_document(workspace_id, document_id)
        return document_view(document)

    @router.post(
        "/workspaces/{workspace_id}/documents/{document_id}/reindex",
        dependencies=[Depends(csrf)],
    )
    async def reindex_document(
        workspace_id: UUID,
        document_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id, admin=True)
        document = await db.scalar(
            select(BusinessDocument)
            .where(
                BusinessDocument.id == document_id,
                BusinessDocument.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if document is None:
            raise HTTPException(404, "Document not found.")
        if document.status in ("processing", "queued", "deleting"):
            raise HTTPException(409, "This document already has a pending job.")
        existing = await db.scalar(
            select(BusinessChunk.id)
            .where(
                BusinessChunk.document_id == document_id,
                BusinessChunk.workspace_id == workspace_id,
            )
            .limit(1)
        )
        if not (document.storage_key or document.content or existing):
            raise HTTPException(
                409, "Upload the original document again; no reusable source remains."
            )
        document.status, document.attempts, document.error, document.lease_id = (
            "queued",
            0,
            None,
            None,
        )
        audit(
            db,
            workspace_id,
            user.id,
            "document.reindex_queued",
            document_id=str(document_id),
        )
        await db.commit()
        await enqueue_document(workspace_id, document_id)
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
            **body.model_dump(mode="json"),
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
        status_code=202,
    )
    async def draft_ticket(
        workspace_id: UUID,
        ticket_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        workspace, _ = await member(db, workspace_id, user.id)
        ticket = await ticket_in_workspace(db, workspace_id, ticket_id, lock=True)
        if ticket.status in ("executed", "resolved", "delivery_pending"):
            raise HTTPException(
                409,
                "This ticket is resolved or awaiting delivery. Reopen resolved tickets before preparing a new draft.",
            )
        if ticket.status == "processing":
            active = await db.scalar(
                select(SupportRun)
                .where(
                    SupportRun.ticket_id == ticket.id,
                    SupportRun.status.in_(["running", "queued"]),
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
        for action in (
            await db.scalars(
                select(ActionRequest).where(
                    ActionRequest.workspace_id == workspace_id,
                    ActionRequest.ticket_id == ticket.id,
                    ActionRequest.status == "proposed",
                )
            )
        ).all():
            action.status = "superseded"
        ticket.status, ticket.last_error = "processing", None
        ticket.revision += 1
        revision = ticket.revision
        run.status = "queued"
        run.job_context = {
            "actor_id": str(user.id),
            "revision": revision,
            "request_id": db.info.get("request_id"),
            "tries": 0,
        }
        ticket.draft, ticket.sources, ticket.tool_calls, ticket.proposed_action = (
            None,
            [],
            [],
            {},
        )
        audit(
            db, workspace_id, user.id, "workflow.queued", ticket_id, run_id=str(run.id)
        )
        await db.commit()
        await enqueue_draft(workspace_id, run.id)
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
        _, role = await member(db, workspace_id, user.id)
        ticket = await ticket_in_workspace(db, workspace_id, ticket_id, lock=True)
        from resolveai.actions import review

        return await review(db, ticket, body, user, role)

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
                **body.model_dump(),
            )
            .on_conflict_do_update(
                constraint="support_feedback_user", set_=body.model_dump()
            )
        )
        await db.commit()
        return {"saved": True}

    @router.get("/workspaces/{workspace_id}/threads/{thread_id}")
    async def thread_messages(
        workspace_id: UUID,
        thread_id: UUID,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        await member(db, workspace_id, user.id)
        thread = await db.scalar(
            select(KnowledgeThread).where(
                KnowledgeThread.id == thread_id,
                KnowledgeThread.workspace_id == workspace_id,
            )
        )
        if thread is None:
            raise HTTPException(404, "Conversation not found.")
        answers = (
            await db.scalars(
                select(KnowledgeAnswer)
                .where(
                    KnowledgeAnswer.thread_id == thread_id,
                    KnowledgeAnswer.workspace_id == workspace_id,
                )
                .order_by(KnowledgeAnswer.created_at.desc(), KnowledgeAnswer.id.desc())
                .limit(50)
            )
        ).all()
        return [
            fields(
                a,
                "id",
                "thread_id",
                "question",
                "answer",
                "sources",
                "mode",
                "created_at",
            )
            for a in reversed(answers)
        ]

    @router.post("/workspaces/{workspace_id}/ask", dependencies=[Depends(csrf)])
    async def ask_knowledge(
        workspace_id: UUID,
        body: Question,
        user: User = Depends(current_user),
        db: AsyncSession = Depends(get_db),
    ):
        workspace, _ = await member(db, workspace_id, user.id)
        thread = None
        history = []
        if body.thread_id:
            thread = await db.scalar(
                select(KnowledgeThread).where(
                    KnowledgeThread.id == body.thread_id,
                    KnowledgeThread.workspace_id == workspace_id,
                )
            )
            if thread is None:
                raise HTTPException(404, "Conversation not found.")
            previous = (
                await db.scalars(
                    select(KnowledgeAnswer)
                    .where(
                        KnowledgeAnswer.thread_id == thread.id,
                        KnowledgeAnswer.workspace_id == workspace_id,
                    )
                    .order_by(KnowledgeAnswer.created_at.desc())
                    .limit(6)
                )
            ).all()
            history = [
                {"question": a.question[:400], "answer": a.answer[:1200]}
                for a in reversed(previous)
            ]
        run = await reserve_run(db, workspace)
        await db.commit()
        run_id = run.id
        service = engine(workspace.ai_mode, workspace.settings)
        started = time.monotonic()
        try:
            async with asyncio.timeout(30):
                sources = await retrieve(
                    db,
                    workspace_id,
                    body.question
                    + (
                        "\nConversation context: "
                        + " ".join(h["question"] for h in history[-2:])
                        if history
                        else ""
                    ),
                    workspace.ai_mode,
                )
                answer = await service.answer(body.question, sources, history)
        except Exception as error:
            await db.rollback()
            await set_tenant(db, workspace_id, for_write=True)
            run = await db.get(SupportRun, run_id)
            if run is None:
                raise HTTPException(
                    401,
                    "Workspace access changed while answering. Reload and sign in again.",
                ) from None
            run.status, run.error = (
                "failed",
                (
                    str(error.detail)[:500]
                    if isinstance(error, HTTPException)
                    else (
                        "Knowledge request timed out after 30 seconds. Retry or ask a person."
                        if isinstance(error, TimeoutError)
                        else "Knowledge request failed. Check provider configuration and retry."
                    )
                ),
            )
            run.model, run.tokens = service.model, service.tokens
            run.duration_ms = round((time.monotonic() - started) * 1000)
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(503, run.error) from error
        # Providers run without a tenant mutation lock. Recheck access and serialize
        # the final write with workspace deletion before publishing an answer.
        await db.commit()
        await set_tenant(db, workspace_id, for_write=True)
        run = await db.scalar(
            select(SupportRun)
            .where(SupportRun.id == run_id, SupportRun.workspace_id == workspace_id)
            .execution_options(populate_existing=True)
        )
        if run is None:
            raise HTTPException(
                401,
                "Workspace access changed while answering. Reload and sign in again.",
            )
        if thread is None:
            thread = KnowledgeThread(
                workspace_id=workspace_id, created_by=user.id, title=body.question[:100]
            )
            db.add(thread)
            await db.flush()
        result = KnowledgeAnswer(
            workspace_id=workspace_id,
            thread_id=thread.id,
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
            result,
            "id",
            "thread_id",
            "question",
            "answer",
            "sources",
            "mode",
            "created_at",
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
