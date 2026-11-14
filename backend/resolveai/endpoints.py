"""Design's active-workspace API, sharing the existing domain handlers."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import Principal, get_principal, require_admin, require_workspace
from database.models import (
    BusinessDocument,
    DemoOrder,
    KnowledgeThread,
    SupportAudit,
    SupportTicket,
    Workspace,
)
from database.pagination import page
from database.repository import TenantRepository
from database.session import get_db
from resolveai.knowledge import retrieve
from resolveai.router import document_view, fields, ticket_summary
from resolveai.schemas import OrderCreate, Question, Review, TicketCreate


def build_scoped_router(existing):
    router = APIRouter(prefix="/api/v1", tags=["Company workspace"])
    handlers = {route.name: route.endpoint for route in existing.routes}

    async def call(name, principal, db, **arguments):
        return await handlers[name](
            workspace_id=require_workspace(principal),
            user=principal.user,
            db=db,
            **arguments,
        )

    @router.get("/documents")
    async def documents(
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        require_admin(principal)
        return await page(
            db, BusinessDocument, document_view, limit=limit, cursor=cursor
        )

    @router.post("/documents", status_code=202)
    async def upload(
        file: UploadFile = File(...),
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("upload_document", principal, db, file=file)

    @router.get("/documents/{document_id}")
    async def document(
        document_id: UUID,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        require_admin(principal)
        return document_view(
            await TenantRepository(db).get(BusinessDocument, document_id)
        )

    @router.delete("/documents/{document_id}", status_code=202)
    async def remove_document(
        document_id: UUID,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("remove_document", principal, db, document_id=document_id)

    @router.post("/documents/{document_id}/reindex")
    async def reindex_document(
        document_id: UUID,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("reindex_document", principal, db, document_id=document_id)

    @router.post("/documents/{document_id}/retry")
    async def retry_document(
        document_id: UUID,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("retry_document", principal, db, document_id=document_id)

    @router.post("/knowledge/search")
    async def search(
        body: Question,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        workspace = await db.get(Workspace, require_workspace(principal))
        return {
            "sources": await retrieve(
                db, workspace.id, body.question, workspace.ai_mode
            )
        }

    @router.post("/knowledge/ask")
    async def ask(
        body: Question,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("ask_knowledge", principal, db, body=body)

    @router.get("/conversations")
    async def conversations(
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        require_workspace(principal)
        return await page(
            db,
            KnowledgeThread,
            lambda row: fields(row, "id", "title", "created_at"),
            limit=limit,
            cursor=cursor,
        )

    @router.get("/conversations/{conversation_id}")
    async def conversation(
        conversation_id: UUID,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("thread_messages", principal, db, thread_id=conversation_id)

    @router.get("/tickets")
    async def tickets(
        status: str | None = None,
        priority: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        require_workspace(principal)
        filters = []
        if status:
            filters.append(SupportTicket.status == status)
        if priority:
            filters.append(SupportTicket.priority == priority)
        return await page(
            db,
            SupportTicket,
            ticket_summary,
            limit=limit,
            cursor=cursor,
            filters=filters,
        )

    @router.post("/tickets", status_code=201)
    async def create_ticket(
        body: TicketCreate,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("create_ticket", principal, db, body=body)

    @router.get("/tickets/{ticket_id}")
    async def ticket(
        ticket_id: UUID,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("get_ticket", principal, db, ticket_id=ticket_id)

    @router.post("/tickets/{ticket_id}/regenerate-draft", status_code=202)
    async def prepare_ticket(
        ticket_id: UUID,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("draft_ticket", principal, db, ticket_id=ticket_id)

    @router.post("/tickets/{ticket_id}/review")
    async def review(
        ticket_id: UUID,
        body: Review,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call(
            "review_ticket", principal, db, ticket_id=ticket_id, body=body
        )

    @router.get("/mock-orders")
    async def mock_orders(
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        require_admin(principal)
        return await page(
            db,
            DemoOrder,
            lambda row: fields(
                row,
                "id",
                "reference",
                "customer_email",
                "item",
                "status",
                "amount_cents",
                "currency",
                "refundable",
                "replaceable",
            ),
            limit=limit,
            cursor=cursor,
        )

    @router.post("/mock-orders", status_code=201)
    async def create_order(
        body: OrderCreate,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        return await call("create_order", principal, db, body=body)

    @router.get("/analytics/overview")
    async def analytics(
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        require_admin(principal)
        snapshot = await call("overview", principal, db)
        return snapshot["metrics"]

    @router.get("/audit-logs")
    async def audit_logs(
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ):
        require_admin(principal)
        return await page(
            db,
            SupportAudit,
            lambda row: fields(
                row, "id", "event", "actor_id", "ticket_id", "details", "created_at"
            ),
            limit=limit,
            cursor=cursor,
        )

    return router
