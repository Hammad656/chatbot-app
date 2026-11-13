"""Background ticket preparation. No consequential action runs in this service."""

import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from ai.gemini import (
    _gemini_http_error,
    _generate_gemini_content,
    configured_gemini_model,
)
from database.models import (
    SupportAudit,
    SupportRun,
    SupportTicket,
    Workspace,
    WorkspaceMember,
)
from database.session import async_session_factory
from database.tenant import set_tenant
from resolveai.workflow import QUEUES, SupportEngine


async def process_ticket_once(factory=async_session_factory, *, workspace_id, run_id):
    async with factory() as db:
        await set_tenant(db, workspace_id)
        run = await db.scalar(
            select(SupportRun)
            .where(SupportRun.id == run_id, SupportRun.workspace_id == workspace_id)
            .with_for_update()
        )
        if run is None or run.status != "queued":
            return False
        context = dict(run.job_context)
        ticket = await db.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.id == run.ticket_id,
                SupportTicket.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        try:
            actor = UUID(context["actor_id"])
        except (KeyError, ValueError, TypeError):
            actor = None
        authorized = await db.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == actor,
            )
        )
        if (
            not authorized
            or ticket is None
            or ticket.revision != context.get("revision")
            or ticket.status != "processing"
            or int(context.get("tries", 0)) >= 3
        ):
            run.status, run.error = (
                "failed",
                "This queued draft was superseded or its requester lost access.",
            )
            if (
                ticket
                and ticket.status == "processing"
                and ticket.revision == context.get("revision")
            ):
                ticket.status, ticket.last_error = (
                    "open",
                    "The queued draft could not continue. Prepare a new draft or review manually.",
                )
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return False
        workspace = await db.get(Workspace, workspace_id)
        mode = workspace.ai_mode
        context["tries"] = int(context.get("tries", 0)) + 1
        context["started_at"] = datetime.now(timezone.utc).isoformat()
        run.job_context, run.status = context, "running"
        initial = {
            "subject": ticket.subject,
            "message": ticket.description,
            "customer_email": ticket.customer_email,
            "order_reference": ticket.order_reference,
        }
        await db.commit()
    engine = SupportEngine(
        mode, _generate_gemini_content, configured_gemini_model, _gemini_http_error
    )
    started, failure = time.monotonic(), None
    try:
        result = await engine.graph(
            db=None, workspace_id=workspace_id, initial=initial, session_factory=factory
        )
    except HTTPException as error:
        failure = error
    except TimeoutError:
        failure = HTTPException(
            503, "The draft exceeded its 30-second budget. Retry or review manually."
        )
    except Exception:
        failure = HTTPException(
            503, "Draft preparation could not reach its tools or providers."
        )
    async with factory() as db:
        await set_tenant(db, workspace_id)
        run = await db.scalar(
            select(SupportRun)
            .where(SupportRun.id == run_id, SupportRun.workspace_id == workspace_id)
            .with_for_update()
        )
        ticket = await db.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.id == run.ticket_id,
                SupportTicket.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if ticket.revision != context["revision"] or ticket.status != "processing":
            run.status, run.error = "failed", "This draft was superseded."
        else:
            run.duration_ms += round((time.monotonic() - started) * 1000)
            run.tokens, run.model = run.tokens + engine.tokens, engine.model
            if failure:
                retry = (
                    failure.status_code in (429, 502, 503)
                    and context["tries"] < 3
                    and not any(
                        term in str(failure.detail)
                        for term in (
                            "HTTP 400",
                            "invalid structured response",
                            "citation",
                            "omitted policy",
                            "GEMINI_API_KEY",
                            "API key",
                        )
                    )
                )
                run.status, run.error = "queued" if retry else "failed", str(
                    failure.detail
                )[:500]
                ticket.status, ticket.last_error = (
                    "processing" if retry else "open"
                ), run.error
                event = "workflow.retry_queued" if retry else "workflow.failed"
            else:
                run.status = "completed"
                run.error = None
                ticket.category, ticket.priority = (
                    result["triage"]["category"],
                    result["triage"]["priority"],
                )
                ticket.queue = QUEUES[ticket.category]
                ticket.status, ticket.last_error = "awaiting_approval", None
                ticket.draft, ticket.proposed_action = (
                    result["draft"],
                    result["proposed_action"],
                )
                ticket.sources, ticket.tool_calls = (
                    result["sources"],
                    result["tool_calls"],
                )
                event = "workflow.prepared"
            db.add(
                SupportAudit(
                    workspace_id=workspace_id,
                    actor_id=actor,
                    ticket_id=ticket.id,
                    event=event,
                    details={
                        "run_id": str(run.id),
                        "request_id": context.get("request_id"),
                    },
                )
            )
        run.completed_at = (
            datetime.now(timezone.utc) if run.status != "queued" else None
        )
        await db.commit()
    return True
