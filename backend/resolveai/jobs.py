"""Redis/arq entrypoints. Postgres rows are durable; Redis dispatches their IDs."""

import logging
import os
import random
from datetime import datetime, timedelta, timezone
from uuid import UUID

from arq import Retry, create_pool, cron
from arq.connections import RedisSettings
from sqlalchemy import DateTime, and_, cast, func, or_, select

from database.models import BusinessDocument, SupportRun, Workspace
from database.session import async_session_factory
from database.tenant import set_tenant
from resolveai.worker import process_once

logger = logging.getLogger(__name__)


async def enqueue_document(workspace_id, document_id):
    url = os.getenv("REDIS_URL")
    if not url:
        return False
    pool = None
    try:
        settings = RedisSettings.from_dsn(url)
        settings.conn_retries, settings.conn_timeout = 0, 1
        pool = await create_pool(settings)
        await pool.enqueue_job("ingest_document", str(workspace_id), str(document_id))
        return True
    except Exception as error:
        # The committed row remains queued; the worker's periodic dispatcher recovers it.
        logger.warning(
            "Redis dispatch unavailable (%s); document remains queued.",
            type(error).__name__,
        )
        return False
    finally:
        if pool:
            await pool.aclose()


async def ingest_document(ctx, workspace_id, document_id):
    workspace_id, document_id = UUID(workspace_id), UUID(document_id)
    await process_once(workspace_id=workspace_id, document_id=document_id)
    async with async_session_factory() as db:
        await set_tenant(db, workspace_id)
        document = await db.get(BusinessDocument, document_id)
        if document and document.status == "failed" and document.attempts < 3:
            document.status = "queued"
            await db.commit()
            raise Retry(defer=2**document.attempts + random.random())


async def enqueue_draft(workspace_id, run_id):
    url = os.getenv("REDIS_URL")
    if not url:
        return False
    pool = None
    try:
        settings = RedisSettings.from_dsn(url)
        settings.conn_retries, settings.conn_timeout = 0, 1
        pool = await create_pool(settings)
        await pool.enqueue_job(
            "prepare_ticket", str(workspace_id), str(run_id), _job_id=f"draft:{run_id}"
        )
        return True
    except Exception as error:
        logger.warning(
            "Redis draft dispatch unavailable (%s); the committed job remains queued.",
            type(error).__name__,
        )
        return False
    finally:
        if pool:
            await pool.aclose()


async def prepare_ticket(ctx, workspace_id, run_id):
    from resolveai.tickets import process_ticket_once

    workspace_id, run_id = UUID(workspace_id), UUID(run_id)
    await process_ticket_once(workspace_id=workspace_id, run_id=run_id)
    async with async_session_factory() as db:
        await set_tenant(db, workspace_id)
        run = await db.get(SupportRun, run_id)
        if run and run.status == "queued":
            raise Retry(
                defer=2 ** int(run.job_context.get("tries", 1)) + random.random()
            )


async def dispatch_pending(ctx):
    async with async_session_factory() as db:
        workspaces = list((await db.scalars(select(Workspace.id))).all())
        for workspace_id in workspaces:
            await set_tenant(db, workspace_id)
            documents = (
                await db.scalars(
                    select(BusinessDocument).where(
                        BusinessDocument.workspace_id == workspace_id,
                        or_(
                            BusinessDocument.status.in_(["queued", "deleting"]),
                            and_(
                                BusinessDocument.status == "processing",
                                BusinessDocument.updated_at
                                < datetime.now(timezone.utc) - timedelta(minutes=12),
                            ),
                        ),
                    )
                )
            ).all()
            for document in documents:
                await ctx["redis"].enqueue_job(
                    "ingest_document",
                    str(workspace_id),
                    str(document.id),
                    _job_id=f"document:{document.id}:{document.status}:{document.attempts}",
                )
            runs = (
                await db.scalars(
                    select(SupportRun).where(
                        SupportRun.workspace_id == workspace_id,
                        SupportRun.ticket_id.is_not(None),
                        or_(
                            SupportRun.status == "queued",
                            and_(
                                SupportRun.status == "running",
                                func.coalesce(
                                    cast(
                                        SupportRun.job_context[
                                            "started_at"
                                        ].as_string(),
                                        DateTime(timezone=True),
                                    ),
                                    SupportRun.created_at,
                                )
                                < datetime.now(timezone.utc) - timedelta(minutes=3),
                            ),
                        ),
                    )
                )
            ).all()
            for run in runs:
                if run.status == "running":
                    run.status = "queued"
                    await db.commit()
                await ctx["redis"].enqueue_job(
                    "prepare_ticket",
                    str(workspace_id),
                    str(run.id),
                    _job_id=f"draft:{run.id}",
                )
            await db.commit()


class WorkerSettings:
    functions = [ingest_document, prepare_ticket]
    cron_jobs = [
        cron(dispatch_pending, second={0, 10, 20, 30, 40, 50}, run_at_startup=True)
    ]
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    max_jobs = 4
    job_timeout = 600
    max_tries = 3
    keep_result = 60
