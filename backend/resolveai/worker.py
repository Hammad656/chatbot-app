"""Idempotent ingestion service called by the Redis-backed worker."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select
from starlette.concurrency import run_in_threadpool

from ai.embeddings import embed_texts
from database.models import BusinessChunk, BusinessDocument, SupportAudit, Workspace
from database.session import async_session_factory
from database.tenant import set_tenant
from resolveai.documents import DocumentChunk
from resolveai.knowledge import parse_document
from resolveai.storage import delete_upload, read_upload

logger = logging.getLogger(__name__)


async def process_once(
    factory=async_session_factory, workspace_id=None, document_id=None
) -> bool:
    async with factory() as db:
        if workspace_id is not None:
            await set_tenant(db, workspace_id)
        document = await db.scalar(
            select(BusinessDocument)
            .where(
                *(
                    [BusinessDocument.workspace_id == workspace_id]
                    if workspace_id
                    else []
                ),
                *([BusinessDocument.id == document_id] if document_id else []),
                or_(
                    BusinessDocument.status == "queued",
                    and_(
                        BusinessDocument.status == "deleting",
                        or_(
                            BusinessDocument.lease_id.is_(None),
                            BusinessDocument.updated_at
                            < datetime.now(timezone.utc) - timedelta(minutes=12),
                        ),
                    ),
                    and_(
                        BusinessDocument.status == "processing",
                        BusinessDocument.updated_at
                        < datetime.now(timezone.utc) - timedelta(minutes=12),
                    ),
                )
            )
            .order_by(BusinessDocument.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if document is None:
            return False
        if document.attempts >= 3:
            document.status = "failed"
            document.error = "Processing stopped after three attempts. Remove the document and upload a corrected copy."
            await db.commit()
            return True
        removing = document.status == "deleting"
        workspace = await db.get(Workspace, document.workspace_id)
        document_id, workspace_id, mode = (
            document.id,
            document.workspace_id,
            workspace.ai_mode,
        )
        filename, content, storage_key = (
            document.filename,
            document.content,
            document.storage_key,
        )
        embedding_model = workspace.embedding_model
        old_chunks = (
            await db.scalars(
                select(BusinessChunk)
                .where(
                    BusinessChunk.document_id == document_id,
                    BusinessChunk.workspace_id == workspace_id,
                )
                .order_by(BusinessChunk.position)
            )
        ).all()
        saved_chunks = [
            DocumentChunk(text=x.text, section=x.section) for x in old_chunks
        ]
        lease = str(uuid4())
        document.status = "deleting" if removing else "processing"
        document.attempts += 1
        document.lease_id = lease
        document.updated_at = datetime.now(timezone.utc)
        await db.commit()
    try:
        async with asyncio.timeout(600):
            if removing:
                chunks, vectors = [], []
            else:
                if content is None and storage_key:
                    content = await run_in_threadpool(read_upload, storage_key)
                if content:
                    chunks = await run_in_threadpool(parse_document, filename, content)
                else:
                    chunks = saved_chunks
                if not chunks or len(chunks) > 500:
                    raise ValueError(
                        "Documents must produce between 1 and 500 passages."
                    )
                vectors = (
                    await embed_texts(
                        [x.section + "\n" + x.text for x in chunks], embedding_model
                    )
                    if mode == "gemini"
                    else [None] * len(chunks)
                )
        async with factory() as db:
            await set_tenant(db, workspace_id)
            current = await db.scalar(
                select(BusinessDocument)
                .where(
                    BusinessDocument.id == document_id,
                    BusinessDocument.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            if current is None or current.lease_id != lease:
                # Lease was superseded; this worker must not overwrite newer state.
                return True
            if removing:
                await db.delete(current)
                event = "document.deleted"
            else:
                await db.execute(
                    delete(BusinessChunk).where(
                        BusinessChunk.document_id == document_id
                    )
                )
                db.add_all(
                    [
                        BusinessChunk(
                            workspace_id=workspace_id,
                            document_id=document_id,
                            position=i,
                            section=chunk.section,
                            text=chunk.text,
                            embedding=vectors[i],
                        )
                        for i, chunk in enumerate(chunks)
                    ]
                )
                current.status, current.chunk_count = "ready", len(chunks)
                current.content, current.error, current.lease_id = None, None, None
                event = "document.ready"
            db.add(
                SupportAudit(
                    workspace_id=workspace_id,
                    event=event,
                    details={"document_id": str(document_id), "chunks": len(chunks)},
                )
            )
            await db.commit()
            if removing:
                await run_in_threadpool(delete_upload, storage_key)
    except Exception as error:
        # A failed job remains durable and visible; no silent switch to local mode.
        message = (
            str(error.detail)[:500]
            if isinstance(error, HTTPException)
            else (
                str(error)[:500]
                if isinstance(error, (ValueError, HTTPException))
                else "Document processing failed. Check provider configuration and retry."
            )
        )
        async with factory() as db:
            await set_tenant(db, workspace_id)
            current = await db.scalar(
                select(BusinessDocument)
                .where(
                    BusinessDocument.id == document_id,
                    BusinessDocument.lease_id == lease,
                )
                .with_for_update()
            )
            if current:
                current.status, current.error, current.lease_id = (
                    "failed",
                    message,
                    None,
                )
                db.add(
                    SupportAudit(
                        workspace_id=workspace_id,
                        event="document.failed",
                        details={
                            "document_id": str(document_id),
                            "error_type": type(error).__name__,
                        },
                    )
                )
                await db.commit()
        logger.warning("Document job failed (%s).", type(error).__name__)
    return True


async def run_worker():
    while True:
        try:
            worked = await process_once()
            await asyncio.sleep(0.1 if worked else 1)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Document worker unavailable (%s). Check DB connectivity and run alembic upgrade head.",
                type(error).__name__,
            )
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(run_worker())
