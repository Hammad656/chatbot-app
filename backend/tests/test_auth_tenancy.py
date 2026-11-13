from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from sqlalchemy import select

from auth.security import REFRESH_COOKIE, token_hash
from database.models import AuthSession, RefreshToken, WorkspaceInvitation


async def company(context, client, name="Company A"):
    created = await client.post("/api/v1/workspaces", json={"name": name})
    assert created.status_code == 201, created.text
    await context.activate(client, created.json()["id"])
    return created.json()["id"]


async def test_auth_has_documented_endpoints_and_requires_access_bearer(
    support_context,
):
    client = await support_context.account()
    schema = (await client.get("/api/v1/openapi.json")).json()
    for path in ("signup", "login", "refresh", "logout", "me"):
        assert f"/api/v1/auth/{path}" in schema["paths"]
    claims = jwt.decode(
        client.headers["Authorization"].split()[1],
        "temporary-integration-test-signing-key",
        algorithms=["HS256"],
    )
    assert claims["exp"] - claims["iat"] == 900
    assert claims["wid"] is None and claims["role"] is None
    client.headers.pop("Authorization")
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert (
        "HttpOnly" in refreshed.headers["set-cookie"]
        and "SameSite=lax" in refreshed.headers["set-cookie"]
    )
    assert "morrow_session=ey" not in refreshed.headers["set-cookie"]
    client.headers["Authorization"] = "Bearer " + refreshed.json()["access_token"]
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_refresh_rotates_hashed_tokens_and_reuse_revokes_family(support_context):
    client = await support_context.account()
    original = client.cookies.get(REFRESH_COOKIE)
    cookie = next(c for c in client.cookies.jar if c.name == REFRESH_COOKIE)
    rotated = await client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200, rotated.text
    assert client.cookies.get(REFRESH_COOKIE) != original
    async with support_context.factory() as db:
        tokens = (await db.scalars(select(RefreshToken))).all()
        assert len(tokens) == 2 and all(t.token_hash != original for t in tokens)
        previous = next(t for t in tokens if t.token_hash == token_hash(original))
        assert previous.used_at is not None
    client.cookies.set(REFRESH_COOKIE, original, domain=cookie.domain, path=cookie.path)
    reused = await client.post("/api/v1/auth/refresh")
    assert reused.status_code == 401
    client.headers["Authorization"] = "Bearer " + rotated.json()["access_token"]
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with support_context.factory() as db:
        assert (await db.scalar(select(AuthSession))).revoked_at is not None


async def test_logout_revokes_access_and_refresh_and_login_reports_wrong_password(
    support_context,
):
    client = await support_context.account("owner@example.com")
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert client.cookies.get(REFRESH_COOKIE) is None
    client.headers.pop("Authorization")
    csrf = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "incorrect"},
    )
    assert bad.status_code == 401 and "incorrect" in bad.json()["error"]["message"]
    good = await client.post(
        "/api/v1/auth/login",
        json={"email": "OWNER@example.com", "password": "temporary-test-password-123"},
    )
    assert good.status_code == 200, good.text
    client.headers["Authorization"] = "Bearer " + good.json()["access_token"]
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_active_workspace_is_signed_and_url_does_not_switch_it(support_context):
    client = await support_context.account()
    a = await company(support_context, client, "Company A")
    b = await company(support_context, client, "Company B")
    claims = jwt.decode(
        client.headers["Authorization"].split()[1],
        "temporary-integration-test-signing-key",
        algorithms=["HS256"],
    )
    assert claims["wid"] == b and claims["role"] == "owner"
    # Being a member of both does not let a path silently change the active tenant.
    assert (await client.get(f"/api/v1/workspaces/{a}/overview")).status_code == 404
    assert (await client.get("/api/v1/workspace/settings")).json()["id"] == b
    await support_context.activate(client, a)
    assert (await client.get(f"/api/v1/workspaces/{a}/overview")).status_code == 200
    assert (await client.get(f"/api/v1/workspaces/{b}/overview")).status_code == 404
    assert (
        await client.patch(
            "/api/v1/workspace/settings", json={"name": "A updated", "workspace_id": b}
        )
    ).status_code == 422
    other = await support_context.account()
    assert (await other.post(f"/api/v1/workspaces/{a}/switch")).status_code == 404


async def test_invitation_creates_cross_account_membership_and_roles_are_live(
    support_context,
):
    owner = await support_context.account("owner@example.com")
    workspace = await company(support_context, owner)
    invited = await owner.post(
        "/api/v1/members/invite", json={"email": "agent@example.com", "role": "agent"}
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["invitation_token"]
    outsider = await support_context.account("outsider@example.com")
    assert (
        await outsider.post("/api/v1/invitations/accept", json={"token": token})
    ).status_code == 404
    agent = await support_context.account("agent@example.com")
    accepted = await agent.post("/api/v1/invitations/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text
    agent.headers["Authorization"] = "Bearer " + accepted.json()["access_token"]
    assert accepted.json()["user"]["active_workspace_id"] == workspace
    assert (
        await agent.get(f"/api/v1/workspaces/{workspace}/overview")
    ).status_code == 200
    assert (await agent.get("/api/v1/members")).status_code == 403
    assert (
        await agent.patch("/api/v1/workspace/settings", json={"name": "Stolen"})
    ).status_code == 403
    members = (await owner.get("/api/v1/members")).json()
    member = next(m for m in members if m["email"] == "agent@example.com")
    assert (
        await owner.patch(f"/api/v1/members/{member['id']}", json={"role": "admin"})
    ).status_code == 204
    # The old agent JWT cannot freeze a stale role: current DB permissions win.
    assert (
        await agent.patch(
            "/api/v1/workspace/settings", json={"name": "Updated by admin"}
        )
    ).status_code == 200
    assert (
        await owner.patch(f"/api/v1/members/{member['id']}", json={"role": "agent"})
    ).status_code == 204
    assert (
        await agent.patch("/api/v1/workspace/settings", json={"name": "Not allowed"})
    ).status_code == 403
    owner_member = next(m for m in members if m["role"] == "owner")
    assert (
        await owner.delete(f"/api/v1/members/{owner_member['id']}")
    ).status_code == 409
    assert (await owner.delete(f"/api/v1/members/{member['id']}")).status_code == 204
    assert (
        await agent.get(f"/api/v1/workspaces/{workspace}/overview")
    ).status_code == 401
    # An already accepted invitation cannot re-add a removed member.
    agent.headers.pop("Authorization")
    again = await agent.post("/api/v1/auth/refresh")
    assert again.status_code == 200
    agent.headers["Authorization"] = "Bearer " + again.json()["access_token"]
    assert (
        await agent.post("/api/v1/invitations/accept", json={"token": token})
    ).status_code == 404


async def test_expired_and_revoked_invites_do_not_grant_access(support_context):
    owner = await support_context.account()
    await company(support_context, owner)
    invited = await owner.post(
        "/api/v1/members/invite", json={"email": "agent@example.com"}
    )
    agent = await support_context.account("agent@example.com")
    async with support_context.factory() as db:
        invite = await db.get(WorkspaceInvitation, UUID(invited.json()["id"]))
        invite.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    assert (
        await agent.post(
            "/api/v1/invitations/accept",
            json={"token": invited.json()["invitation_token"]},
        )
    ).status_code == 404
    fresh = await owner.post(
        "/api/v1/members/invite", json={"email": "agent@example.com"}
    )
    assert (
        await owner.delete(f"/api/v1/invitations/{fresh.json()['id']}")
    ).status_code == 204
    assert (
        await agent.post(
            "/api/v1/invitations/accept",
            json={"token": fresh.json()["invitation_token"]},
        )
    ).status_code == 404
