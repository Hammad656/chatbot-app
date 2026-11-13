import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai.gemini import (
    _gemini_http_error,
    _generate_gemini_content,
    configured_gemini_model,
)
from database.session import engine
from resolveai.router import build_router
from resolveai.endpoints import build_scoped_router
from auth.router import router as auth_router
from auth.security import require_csrf, session_secret
from auth.dependencies import get_current_user
from workspaces.router import router as workspace_router
from fastapi.responses import RedirectResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def configured_origins() -> list[str]:
    origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(_: FastAPI):
    from resolveai.worker import run_worker

    worker = None
    if os.getenv("RESOLVEAI_INLINE_WORKER", "false").lower() == "true":
        worker = asyncio.create_task(run_worker())
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await engine.dispose()


app = FastAPI(
    title="ResolveAI API",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)


@app.middleware("http")
async def request_identity(request: Request, call_next):
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, error: StarletteHTTPException):
    return JSONResponse(
        status_code=error.status_code,
        headers=error.headers,
        content={
            "error": {
                "code": f"http_{error.status_code}",
                "message": str(error.detail),
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "; ".join(item["msg"] for item in error.errors()),
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=[
        "Content-Type",
        "X-CSRF-Token",
        "Authorization",
        "Idempotency-Key",
        "X-Workspace-ID",
    ],
)


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(_: Request, error: SQLAlchemyError):
    logger.error(
        "Database request failed (%s). Check connectivity and migrations.",
        type(error).__name__,
    )
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "database_unavailable",
                "message": "Database unavailable or schema not ready. Check PostgreSQL and run alembic upgrade head from backend.",
                "request_id": getattr(_.state, "request_id", None),
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "Unhandled request error (request_id=%s, type=%s).",
        request_id,
        type(error).__name__,
    )
    return JSONResponse(
        status_code=500,
        headers={"X-Request-ID": request_id or "", "Cache-Control": "no-store"},
        content={
            "error": {
                "code": "internal_error",
                "message": "The request could not be completed. Please retry or contact your workspace administrator.",
                "request_id": request_id,
            }
        },
    )


@app.get("/health")
@app.get("/api/v1/health")
async def health_check():
    postgres_password = os.getenv("POSTGRES_PASSWORD")
    return {
        "status": "ok",
        "ai_configured": bool(os.getenv("GEMINI_API_KEY")),
        "gemini_model": configured_gemini_model(),
        "database_configured": bool(
            postgres_password
            and postgres_password != "replace-with-your-postgres-password"
        ),
        "knowledge_store": "pgvector",
        "embedding_dimension": 768,
        "knowledge_rag_configured": bool(os.getenv("GEMINI_API_KEY")),
    }


@app.get("/api/v1/health/ready")
@app.get("/health/ready", include_in_schema=False)
async def readiness():
    session_secret()
    from sqlalchemy import text

    from database.session import async_session_factory

    async with async_session_factory() as db:
        state = (
            await db.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector'), rolsuper OR rolbypassrls OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relowner=r.oid AND c.relname='support_chunks' AND n.nspname=current_schema()) FROM pg_roles r WHERE rolname=current_user"
                )
            )
        ).one()
        if not state[0]:
            raise HTTPException(
                503, "Install pgvector and run the ResolveAI migrations."
            )
        if os.getenv("RESOLVEAI_REQUIRE_RLS", "false").lower() == "true" and state[1]:
            raise HTTPException(
                503,
                "The API must connect as a non-owner role without RLS bypass privileges.",
            )
        if not await db.scalar(text("SELECT to_regclass('support_knowledge_threads')")):
            raise HTTPException(503, "Run alembic upgrade head.")
        if not await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='support_runs'::regclass AND attname='job_context' AND NOT attisdropped)"
            )
        ):
            raise HTTPException(
                503, "Run alembic upgrade head for the background job schema."
            )
    async with async_session_factory() as db:
        if not await db.scalar(text("SELECT to_regclass('refresh_tokens')")):
            raise HTTPException(
                503,
                "Run alembic upgrade head for authentication and workspace security.",
            )
    if os.getenv("REDIS_URL"):
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = None
        try:
            settings = RedisSettings.from_dsn(os.environ["REDIS_URL"])
            settings.conn_retries, settings.conn_timeout = 0, 1
            pool = await create_pool(settings)
            await pool.ping()
        except Exception:
            raise HTTPException(
                503, "Redis is unavailable. Start the Redis service and worker."
            ) from None
        finally:
            if pool:
                await pool.aclose()
    return {
        "status": "ready",
        "knowledge_store": "pgvector",
        "rls_enforced": not state[1],
    }


app.include_router(auth_router)
app.include_router(workspace_router)


@app.get("/docs", include_in_schema=False)
async def api_reference():
    return RedirectResponse("/api/v1/docs")


# Reuse the existing session/CSRF boundary without circular router imports.
support_router = build_router(
    get_current_user,
    require_csrf,
    _generate_gemini_content,
    configured_gemini_model,
    _gemini_http_error,
)
app.include_router(support_router)
app.include_router(build_scoped_router(support_router))
