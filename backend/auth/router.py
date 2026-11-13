import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from auth.dependencies import Principal, get_principal
from auth.schemas import Login, Signup, TokenResponse, UserView
from auth.security import (
    CSRF_COOKIE,
    DUMMY_PASSWORD_HASH,
    PASSWORD_HASHER,
    REFRESH_COOKIE,
    REFRESH_SECONDS,
    clear_cookies,
    require_csrf,
    secure_cookie,
    session_secret,
    token_hash,
)
from auth.service import (
    access_response,
    issue_refresh,
    new_session,
    revoke_family,
    security_event,
)
from database.models import AuthSession, RefreshToken, User
from database.session import get_db
from resolveai.limits import rate_limit

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


def invalid_session(request, message="Your session expired. Sign in again."):
    response = JSONResponse(
        status_code=401,
        content={
            "error": {
                "code": "invalid_session",
                "message": message,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )
    clear_cookies(response)
    return response


@router.get("/csrf")
async def csrf_token(request: Request, response: Response):
    token = request.cookies.get(CSRF_COOKIE)
    if not token or len(token) < 32 or len(token) > 128:
        token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=REFRESH_SECONDS,
        httponly=False,
        secure=secure_cookie(),
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": token}


@router.post(
    "/signup",
    status_code=201,
    response_model=TokenResponse,
    dependencies=[Depends(require_csrf)],
)
async def signup(
    body: Signup,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    session_secret()
    await rate_limit(
        "signup", request.client.host if request.client else "unknown", 10, 600
    )
    email = str(body.email).strip().lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(
            409, "An account with this email already exists. Sign in instead."
        )
    user = User(
        email=email,
        full_name=body.full_name.strip(),
        password_hash=await run_in_threadpool(PASSWORD_HASHER.hash, body.password),
    )
    db.add(user)
    try:
        await db.flush()
        security_event(db, request, "auth.signup", user.id)
        return await new_session(db, user, request, response)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409, "An account with this email already exists. Sign in instead."
        ) from None


@router.post(
    "/login", response_model=TokenResponse, dependencies=[Depends(require_csrf)]
)
async def login(
    body: Login,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    session_secret()
    email = str(body.email).strip().lower()
    await rate_limit(
        "login",
        (request.client.host if request.client else "unknown") + ":" + email,
        8,
        60,
    )
    user = await db.scalar(select(User).where(User.email == email))
    try:
        matches = await run_in_threadpool(
            PASSWORD_HASHER.verify,
            body.password,
            user.password_hash if user else DUMMY_PASSWORD_HASH,
        )
    except ValueError:
        matches = False
    if not user or not user.is_active or not matches:
        security_event(db, request, "auth.login_failed", user.id if user else None)
        await db.commit()
        raise HTTPException(401, "Email or password is incorrect.")
    return await new_session(db, user, request, response)


@router.post(
    "/refresh", response_model=TokenResponse, dependencies=[Depends(require_csrf)]
)
async def refresh(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    session_secret()
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw or len(raw) > 128:
        return invalid_session(request)
    token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))
    )
    if token is None:
        return invalid_session(request)
    family = await db.scalar(
        select(AuthSession).where(AuthSession.id == token.family_id).with_for_update()
    )
    token = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.id == token.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(timezone.utc)
    if token.used_at:
        await revoke_family(db, family)
        security_event(db, request, "auth.refresh_reuse", token.user_id)
        await db.commit()
        return invalid_session(
            request,
            "A reused refresh token was detected. This session has been revoked; sign in again.",
        )
    user = await db.get(User, token.user_id)
    if (
        not user
        or not user.is_active
        or token.revoked_at
        or token.expires_at <= now
        or family.revoked_at
        or family.expires_at <= now
    ):
        return invalid_session(request)
    token.used_at = now
    result = await access_response(db, user, family)
    await issue_refresh(db, user, family, response)
    security_event(db, request, "auth.refreshed", user.id)
    await db.commit()
    return result


@router.get("/me", response_model=UserView)
async def me(principal: Principal = Depends(get_principal)):
    return UserView.model_validate(principal.user).model_copy(
        update={"active_workspace_id": principal.workspace_id, "role": principal.role}
    )


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
async def logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        token = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))
        )
        if token:
            family = await db.scalar(
                select(AuthSession)
                .where(AuthSession.id == token.family_id)
                .with_for_update()
            )
            await revoke_family(db, family)
            security_event(db, request, "auth.logout", token.user_id)
            await db.commit()
    clear_cookies(response)
    response.status_code = 204
    return response
