import asyncio
from unittest.mock import AsyncMock, patch
from uuid import UUID

from sqlalchemy import func, select

from auth.security import REFRESH_COOKIE
from database.models import AuthenticationEvent, BusinessDocument, SupportTicket, User
from resolveai.storage import path_for
from resolveai.worker import process_once


async def company(context, client, name="Company A"):
    created = await client.post("/api/v1/workspaces", json={"name": name})
    assert created.status_code == 201, created.text
    await context.activate(client, created.json()["id"])
    return created.json()["id"]


async def test_active_workspace_resource_routes_and_cursors_are_isolated(
    support_context,
):
    owner = await support_context.account()
    workspace = await company(support_context, owner)
    docs = []
    for number in range(2):
        uploaded = await owner.post(
            "/api/v1/documents",
            files={
                "file": (
                    f"policy-{number}.md",
                    f"# Shipping\nShipping takes {number+3} business days.".encode(),
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 202, uploaded.text
        docs.append(uploaded.json()["id"])
        assert await process_once(
            support_context.factory,
            workspace_id=UUID(workspace),
            document_id=UUID(docs[-1]),
        )
    listed = await owner.get("/api/v1/documents?limit=1")
    assert len(listed.json()["items"]) == 1 and listed.json()["next_cursor"]
    cursor = listed.json()["next_cursor"]
    next_page = (
        await owner.get("/api/v1/documents", params={"limit": 1, "cursor": cursor})
    ).json()
    assert next_page["items"][0]["id"] != listed.json()["items"][0]["id"]
    assert next_page["next_cursor"] is None
    assert (await owner.get("/api/v1/documents/" + docs[0])).status_code == 200
    assert (
        await owner.post(
            "/api/v1/knowledge/search", json={"question": "Shipping timeline?"}
        )
    ).json()["sources"]
    answered = await owner.post(
        "/api/v1/knowledge/ask", json={"question": "Shipping timeline?"}
    )
    assert answered.status_code == 200, answered.text
    thread = answered.json()["thread_id"]
    ticket = await owner.post(
        "/api/v1/tickets",
        json={
            "subject": "Shipping question",
            "description": "When does shipping arrive?",
            "customer_email": "customer@example.com",
        },
    )
    assert ticket.status_code == 201, ticket.text
    ticket_id = ticket.json()["id"]
    assert (await owner.get("/api/v1/tickets")).json()["items"][0]["id"] == ticket_id
    assert (await owner.get("/api/v1/conversations")).json()["items"][0]["id"] == thread
    other = await support_context.account()
    await company(support_context, other, "Other company")
    for path in (
        f"/documents/{docs[0]}",
        f"/tickets/{ticket_id}",
        f"/conversations/{thread}",
    ):
        assert (await other.get("/api/v1" + path)).status_code == 404
    for path in (
        f"/documents/{docs[0]}/retry",
        f"/documents/{docs[0]}/reindex",
        f"/tickets/{ticket_id}/regenerate-draft",
    ):
        assert (await other.post("/api/v1" + path)).status_code == 404
    assert (await other.delete("/api/v1/documents/" + docs[0])).status_code == 404
    assert (
        await other.get("/api/v1/documents", params={"cursor": cursor})
    ).status_code == 404
    for path in (
        "/documents",
        "/tickets",
        "/conversations",
        "/mock-orders",
        "/audit-logs",
    ):
        response = await other.get("/api/v1" + path)
        assert response.status_code == 200, response.text
        assert not response.json()["items"] or path == "/audit-logs"
    assert (
        await other.post(
            "/api/v1/knowledge/search", json={"question": "Shipping timeline?"}
        )
    ).json()["sources"] == []
    assert (
        await owner.get(
            "/api/v1/workspace/settings",
            headers={
                "X-Workspace-ID": (
                    await other.get("/api/v1/workspace/settings")
                ).json()["id"]
            },
        )
    ).status_code == 404


async def test_only_owner_can_delete_and_other_accounts_and_workspaces_survive(
    support_context,
):
    owner = await support_context.account("owner@example.com")
    a = await company(support_context, owner, "Disposable company")
    upload = await owner.post(
        "/api/v1/documents",
        files={
            "file": (
                "private.md",
                b"# Policy\nCompany knowledge only.",
                "text/markdown",
            )
        },
    )
    assert upload.status_code == 202, upload.text
    async with support_context.factory() as db:
        doc = await db.get(BusinessDocument, UUID(upload.json()["id"]))
        private_file = path_for(doc.storage_key)
        assert private_file.exists()
    invite = await owner.post(
        "/api/v1/members/invite", json={"email": "admin@example.com", "role": "admin"}
    )
    admin = await support_context.account("admin@example.com")
    accepted = await admin.post(
        "/api/v1/invitations/accept", json={"token": invite.json()["invitation_token"]}
    )
    admin.headers["Authorization"] = "Bearer " + accepted.json()["access_token"]
    assert (
        await admin.request(
            "DELETE",
            f"/api/v1/workspaces/{a}",
            json={"confirmation_name": "Disposable company"},
        )
    ).status_code == 403
    b = await company(support_context, owner, "Retained company")
    assert (
        await owner.request(
            "DELETE",
            f"/api/v1/workspaces/{a}",
            json={"confirmation_name": "Disposable company"},
        )
    ).status_code == 404
    await support_context.activate(owner, a)
    assert (
        await owner.request(
            "DELETE",
            f"/api/v1/workspaces/{a}",
            json={"confirmation_name": "wrong name"},
        )
    ).status_code == 409
    deleted = await owner.request(
        "DELETE",
        f"/api/v1/workspaces/{a}",
        json={"confirmation_name": "Disposable company"},
    )
    assert deleted.status_code == 200, deleted.text
    owner.headers["Authorization"] = "Bearer " + deleted.json()["access_token"]
    assert deleted.json()["user"]["active_workspace_id"] == b
    assert not private_file.exists()
    assert [w["id"] for w in (await owner.get("/api/v1/workspaces")).json()] == [b]
    assert (await admin.get("/api/v1/workspace/settings")).status_code == 401
    async with support_context.factory() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 2
        event = await db.scalar(
            select(AuthenticationEvent).where(
                AuthenticationEvent.event == "workspace.deleted"
            )
        )
        assert event.subject_id == a


async def test_concurrent_refresh_is_serialized_and_reuse_fails_closed(support_context):
    client = await support_context.account()
    assert client.cookies.get(REFRESH_COOKIE)
    results = await asyncio.gather(
        client.post("/api/v1/auth/refresh"), client.post("/api/v1/auth/refresh")
    )
    assert sorted(r.status_code for r in results) == [200, 401]
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_deleted_workspace_cancels_inflight_draft_without_execution(
    support_context,
):
    from resolveai.tickets import process_ticket_once

    owner = await support_context.account()
    workspace = await company(support_context, owner, "Company to remove")
    ticket = await owner.post(
        "/api/v1/tickets",
        json={
            "subject": "Need help",
            "description": "Help with my company order please",
            "customer_email": "customer@example.com",
        },
    )
    queued = await owner.post(f"/api/v1/tickets/{ticket.json()['id']}/regenerate-draft")
    assert queued.status_code == 202, queued.text

    async def deleting(*args, **kwargs):
        response = await owner.request(
            "DELETE",
            f"/api/v1/workspaces/{workspace}",
            json={"confirmation_name": "Company to remove"},
        )
        assert response.status_code == 200, response.text
        return {}

    with patch(
        "resolveai.workflow.SupportEngine.graph", AsyncMock(side_effect=deleting)
    ):
        assert not await process_ticket_once(
            support_context.factory,
            workspace_id=UUID(workspace),
            run_id=UUID(queued.json()["runs"][0]["id"]),
        )
    async with support_context.factory() as db:
        assert await db.scalar(select(func.count()).select_from(SupportTicket)) == 0
