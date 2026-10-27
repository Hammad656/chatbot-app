import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from database.models import AuthSession, ChatMessage, Conversation, User
from database.session import engine, get_db

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
SESSION_COOKIE = "morrow_session"
CSRF_COOKIE = "morrow_csrf"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
PASSWORD_HASHER = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
SYSTEM_INSTRUCTIONS = """You are Morrow, a thoughtful, capable general-purpose AI assistant.
Help with the user's actual question across topics: explain clearly, reason carefully,
write and brainstorm creatively, and adapt your level of detail to what they need.
Be warm, direct, and well organized. For complex requests, break the answer into
useful steps. Ask a brief clarifying question when essential context is missing.
Be honest about uncertainty; never invent facts, sources, or capabilities. Avoid
unnecessary disclaimers and filler. Use plain language and readable formatting."""


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    created_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse]


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    content: str = Field(min_length=1, max_length=12_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        content = value.strip()
        if not content:
            raise ValueError("Message content cannot be blank.")
        return content


class CreateChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse] = Field(default_factory=list)


class AssistantResponse(BaseModel):
    answer: str
    message: MessageResponse


def configured_origins() -> list[str]:
    origins = os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    )
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def session_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured. Set JWT_SECRET_KEY in backend/.env.",
        )
    return secret


async def set_session_cookies(
    response: Response, user: User, db: AsyncSession
) -> None:
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
    if not cookie_token or not header_token or not secrets.compare_digest(
        cookie_token, header_token
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
    yield
    await engine.dispose()


app = FastAPI(title="Morrow Chat API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.get("/health")
async def health_check():
    postgres_password = os.getenv("POSTGRES_PASSWORD")
    return {
        "status": "ok",
        "ai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "database_configured": bool(
            postgres_password
            and postgres_password != "replace-with-your-postgres-password"
        ),
    }


@app.get("/api/auth/csrf")
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


@app.post("/api/auth/signup", response_model=UserResponse, status_code=201)
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


@app.post("/api/auth/login", response_model=UserResponse)
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


@app.get("/api/auth/me", response_model=UserResponse)
async def current_user(
    user: Annotated[User, Depends(get_current_user)],
):
    return user


@app.post("/api/auth/logout", status_code=204)
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


@app.get("/api/chats", response_model=list[ChatResponse])
async def list_chats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_database_password()
    chats = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .options(selectinload(Conversation.messages))
        .order_by(Conversation.updated_at.desc())
    )
    results = []
    for chat in chats:
        results.append(
            ChatResponse(
                id=chat.id,
                title=chat.title,
                created_at=chat.created_at,
                updated_at=chat.updated_at,
                messages=list(chat.messages),
            )
        )
    return results


@app.post("/api/chats", response_model=CreateChatResponse, status_code=201)
async def create_chat(
    _: Annotated[None, Depends(require_csrf)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_database_password()
    chat = Conversation(user_id=user.id)
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return CreateChatResponse(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


@app.delete("/api/chats/{conversation_id}", status_code=204)
async def delete_chat(
    conversation_id: UUID,
    _: Annotated[None, Depends(require_csrf)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_database_password()
    chat = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        )
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    await db.delete(chat)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/chat/{conversation_id}", response_model=AssistantResponse)
async def chat(
    conversation_id: UUID,
    request: SendMessageRequest,
    _: Annotated[None, Depends(require_csrf)],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    require_database_password()
    if request.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conversation ID in the URL and request body must match.",
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The assistant is not configured yet. Add OPENAI_API_KEY to backend/.env.",
        )

    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    existing_messages = list(
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(29)
        )
    )
    history = [
        {"role": message.role, "content": message.content}
        for message in reversed(existing_messages)
    ]

    user_message = ChatMessage(
        conversation_id=conversation.id,
        role="user",
        content=request.content.strip(),
    )
    if conversation.title == "New conversation":
        title = request.content.strip()
        conversation.title = title[:77] + ("..." if len(title) > 80 else "")
    conversation.updated_at = datetime.now(timezone.utc)
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)
    history.append({"role": "user", "content": request.content.strip()})

    client = AsyncOpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
        max_retries=1,
    )
    try:
        response = await client.responses.create(
            model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            instructions=SYSTEM_INSTRUCTIONS,
            input=history,
        )
    except OpenAIError as error:
        logger.exception("OpenAI request failed.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI service could not complete your request. Please try again.",
        ) from error
    finally:
        await client.close()

    answer = response.output_text.strip()
    if not answer:
        logger.error("OpenAI returned an empty response.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI service returned an empty response. Please try again.",
        )

    assistant_message = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
    )
    db.add(assistant_message)
    await db.commit()
    await db.refresh(assistant_message)
    await db.refresh(conversation)

    return AssistantResponse(
        answer=answer,
        message=assistant_message,
    )
