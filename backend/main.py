import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID, uuid4

import jwt
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai.gemini import (
    _gemini_http_error,
    _generate_gemini_content,
    configured_gemini_model,
)
from database.models import AuthSession, User
from database.session import engine, get_db
from database.tenant import set_tenant
from resolveai.router import build_router

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

SESSION_COOKIE = "morrow_session"
CSRF_COOKIE = "morrow_csrf"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
PASSWORD_HASHER = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    created_at: datetime


def configured_origins() -> list[str]:
    origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def session_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured. Set JWT_SECRET_KEY in backend/.env.",
        )
    return secret


async def set_session_cookies(response: Response, user: User, db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=SESSION_MAX_AGE)
    auth_session = AuthSession(user_id=user.id, expires_at=expires_at)
    db.add(auth_session)
    await db.commit()
    token = jwt.encode(
        {
            "sub": str(user.id),
            "jti": str(auth_session.id),
            "iat": now,
            "exp": expires_at,
        },
        session_secret(),
        algorithm="HS256",
    )
    secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def require_csrf(request: Request) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("X-CSRF-Token", "")
    if (
        not cookie_token
        or not header_token
        or not secrets.compare_digest(cookie_token, header_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The security token is missing or invalid. Refresh and try again.",
        )


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    require_database_password()
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please log in to continue.",
        )
    try:
        payload = jwt.decode(token, session_secret(), algorithms=["HS256"])
        user_id = UUID(payload["sub"])
        session_id = UUID(payload["jti"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please log in again.",
        ) from error

    now = datetime.now(timezone.utc)
    auth_session = await db.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    user = await db.get(User, user_id)
    if user is None or auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is no longer valid. Please log in again.",
        )
    await set_tenant(db, actor_id=user.id)
    db.info["request_id"] = getattr(request.state, "request_id", None)
    return user


def require_database_password() -> None:
    password = os.getenv("POSTGRES_PASSWORD")
    if not password or password == "replace-with-your-postgres-password":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured. Set POSTGRES_PASSWORD in backend/.env.",
        )


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
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
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
async def readiness():
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


@app.get("/api/v1/auth/csrf")
async def csrf_token(response: Response):
    secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return {"csrf_token": token}


@app.post("/api/v1/auth/signup", response_model=UserResponse, status_code=201)
async def signup(
    credentials: Credentials,
    response: Response,
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_database_password()
    email = str(credentials.email).strip().lower()
    existing_user = await db.scalar(select(User).where(User.email == email))
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    password_hash = await run_in_threadpool(PASSWORD_HASHER.hash, credentials.password)
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from error

    await db.refresh(user)
    await set_session_cookies(response, user, db)
    return user


@app.post("/api/v1/auth/login", response_model=UserResponse)
async def login(
    credentials: Credentials,
    response: Response,
    _: Annotated[None, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_database_password()
    email = str(credentials.email).strip().lower()
    user = await db.scalar(select(User).where(User.email == email))
    password_matches = await run_in_threadpool(
        PASSWORD_HASHER.verify,
        credentials.password,
        user.password_hash if user else DUMMY_PASSWORD_HASH,
    )
    if user is None or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )

    await set_session_cookies(response, user, db)
    return user


@app.get("/api/v1/auth/me", response_model=UserResponse)
async def current_user(
    user: Annotated[User, Depends(get_current_user)],
):
    return user


@app.post("/api/v1/auth/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    _: Annotated[None, Depends(require_csrf)],
    __: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    token = request.cookies[SESSION_COOKIE]
    payload = jwt.decode(token, session_secret(), algorithms=["HS256"])
    auth_session = await db.get(AuthSession, UUID(payload["jti"]))
    if auth_session is not None:
        auth_session.revoked_at = datetime.now(timezone.utc)
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", httponly=False, samesite="strict")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# Reuse the existing session/CSRF boundary without circular router imports.
app.include_router(
    build_router(
        get_current_user,
        require_csrf,
        _generate_gemini_content,
        configured_gemini_model,
        _gemini_http_error,
    )
)
