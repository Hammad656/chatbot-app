import asyncio
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import func, select

from database.models import DemoOrder, SupportDecision, SupportMessage
from resolveai.worker import process_once
from resolveai.workflow import local_triage


async def create_workspace(client):
    response = await client.post("/api/v1/workspaces", json={"name": "Test business"})
    assert response.status_code == 201, response.text
    switch = await client.post(f"/api/v1/workspaces/{response.json()['id']}/switch")
    assert switch.status_code == 200, switch.text
    client.headers["Authorization"] = "Bearer " + switch.json()["access_token"]
    return response.json()["id"]


async def demo(context, client, workspace):
    prefix = f"/api/v1/workspaces/{workspace}"
    assert (await client.post(prefix + "/demo")).status_code == 200
    for _ in range(4):
        if not await process_once(context.factory):
            break
    return (await client.get(prefix + "/overview")).json()


async def prepare_draft(context, client, path):
    from resolveai.tickets import process_ticket_once

    response = await client.post(path + "/draft")
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["status"] == "processing" and queued["draft"] is None
    workspace_id = UUID(path.split("/workspaces/")[1].split("/")[0])
    for _ in range(3):
        await process_ticket_once(
            context.factory,
            workspace_id=workspace_id,
            run_id=UUID(queued["runs"][0]["id"]),
        )
        response = await client.get(path)
        if response.json()["runs"][0]["status"] != "queued":
            break
    return response


async def test_background_draft_acknowledges_and_replays_without_repreparing(
    support_context,
):
    from resolveai.tickets import process_ticket_once

    client = await support_context.account()
    workspace = await create_workspace(client)
    snapshot = await demo(support_context, client, workspace)
    ticket = next(t for t in snapshot["tickets"] if t["subject"] == "Request a refund")
    path = f"/api/v1/workspaces/{workspace}/tickets/{ticket['id']}"
    queued = await client.post(path + "/draft")
    assert queued.status_code == 202 and queued.json()["draft"] is None
    run = UUID(queued.json()["runs"][0]["id"])
    assert await process_ticket_once(
        support_context.factory, workspace_id=UUID(workspace), run_id=run
    )
    assert not await process_ticket_once(
        support_context.factory, workspace_id=UUID(workspace), run_id=run
    )
    detail = (await client.get(path)).json()
    assert detail["status"] == "awaiting_approval"
    state = (await client.get(f"/api/v1/workspaces/{workspace}/overview")).json()
    assert (
        next(o for o in state["orders"] if o["reference"] == "ORD-1003")["status"]
        == "delivered"
    )


async def test_queued_draft_losing_requester_access_stops_before_model(support_context):
    from sqlalchemy import delete

    from database.models import WorkspaceMember
    from resolveai.tickets import process_ticket_once

    owner, agent = await support_context.account(), await support_context.account()
    workspace = await create_workspace(owner)
    snapshot = await demo(support_context, owner, workspace)
    user = (await agent.get("/api/v1/auth/me")).json()
    assert (
        await owner.post(
            f"/api/v1/workspaces/{workspace}/members",
            json={"email": user["email"], "role": "agent"},
        )
    ).status_code == 201
    ticket = snapshot["tickets"][0]
    await support_context.activate(agent, workspace)
    path = f"/api/v1/workspaces/{workspace}/tickets/{ticket['id']}"
    queued = await agent.post(path + "/draft")
    assert queued.status_code == 202
    async with support_context.factory() as db:
        await db.execute(
            delete(WorkspaceMember).where(
                WorkspaceMember.workspace_id == UUID(workspace),
                WorkspaceMember.user_id == UUID(user["id"]),
            )
        )
        await db.commit()
    with patch("resolveai.workflow.SupportEngine.graph", AsyncMock()) as model:
        await process_ticket_once(
            support_context.factory,
            workspace_id=UUID(workspace),
            run_id=UUID(queued.json()["runs"][0]["id"]),
        )
        model.assert_not_awaited()
    detail = (await owner.get(path)).json()
    assert detail["status"] == "open" and detail["runs"][0]["status"] == "failed"


async def prepared(context, subject="Request a refund"):
    client = await context.account()
    workspace = await create_workspace(client)
    snapshot = await demo(context, client, workspace)
    ticket = next(t for t in snapshot["tickets"] if t["subject"] == subject)
    path = f"/api/v1/workspaces/{workspace}/tickets/{ticket['id']}"
    response = await prepare_draft(context, client, path)
    assert response.status_code == 200, response.text
    return client, workspace, path, response.json()


def review_body(ticket, **extra):
    return {
        "request_id": str(uuid4()),
        "expected_revision": ticket["revision"],
        "decision": "approve",
        "reply": ticket["draft"],
        **extra,
    }


async def test_human_gate_and_order_tool(support_context):
    client, workspace, path, ticket = await prepared(support_context)
    assert ticket["status"] == "awaiting_approval" and ticket["category"] == "refund"
    assert (
        ticket["queue"] == "Billing" and ticket["proposed_action"]["kind"] == "refund"
    )
    assert ticket["sources"] and ticket["tool_calls"][0]["name"] == "lookup_order"
    snapshot = (await client.get(f"/api/v1/workspaces/{workspace}/overview")).json()
    assert (
        next(o for o in snapshot["orders"] if o["reference"] == "ORD-1003")["status"]
        == "delivered"
    )
    response = await client.post(
        path + "/review",
        json=review_body(ticket, reply="An agent has reviewed this request."),
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"]["result"]["mock"] is True
    assert response.json()["ticket"]["status"] == "executed"
    assert (
        response.json()["ticket"]["messages"][-1]["content"]
        == "An agent has reviewed this request."
    )


async def test_concurrent_idempotency(support_context):
    client, _, path, ticket = await prepared(support_context)
    body = review_body(ticket)
    results = await asyncio.gather(
        client.post(path + "/review", json=body),
        client.post(path + "/review", json=body),
    )
    assert [r.status_code for r in results] == [200, 200]
    assert sorted(r.json()["replayed"] for r in results) == [False, True]
    assert (
        await client.post(path + "/review", json={**body, "request_id": str(uuid4())})
    ).json()["replayed"] is True
    assert (
        await client.post(path + "/review", json={**body, "reply": "Changed payload"})
    ).status_code == 409
    async with support_context.factory() as db:
        assert await db.scalar(select(func.count()).select_from(SupportDecision)) == 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(SupportMessage)
                .where(SupportMessage.role == "agent")
            )
            == 1
        )


async def test_tenant_isolation(support_context):
    owner, workspace, path, ticket = await prepared(support_context)
    other = await support_context.account()
    other_workspace = await create_workspace(other)
    prefix = f"/api/v1/workspaces/{workspace}"
    assert (await other.get(prefix + "/overview")).status_code == 404
    assert (await other.get(path)).status_code == 404
    assert (
        await other.post(prefix + "/ask", json={"question": "Refund policy?"})
    ).status_code == 404
    assert (
        await other.post(path + "/review", json=review_body(ticket))
    ).status_code == 404
    assert (await other.post(path + "/draft")).status_code == 404
    assert (
        await owner.get(f"/api/v1/workspaces/{other_workspace}/tickets/{ticket['id']}")
    ).status_code == 404
    assert (
        await other.delete(prefix + "/documents/" + ticket["sources"][0]["document_id"])
    ).status_code == 404


async def test_agent_permissions(support_context):
    owner, workspace, path, ticket = await prepared(support_context)
    agent = await support_context.account("agent@example.com")
    prefix = f"/api/v1/workspaces/{workspace}"
    assert (
        await owner.post(
            prefix + "/members", json={"email": "agent@example.com", "role": "agent"}
        )
    ).status_code == 201
    await support_context.activate(agent, workspace)
    assert (await agent.get(prefix + "/overview")).status_code == 200
    restricted = (await agent.get(prefix + "/overview")).json()
    assert (
        restricted["orders"] == []
        and restricted["audit"] == []
        and restricted["members"] == []
    )
    assert (
        await agent.post(path + "/review", json=review_body(ticket))
    ).status_code == 403
    assert (
        await agent.post(
            prefix + "/documents",
            files={"file": ("policy.txt", b"Policy", "text/plain")},
        )
    ).status_code == 403
    assert (
        await agent.post(
            prefix + "/members", json={"email": "agent@example.com", "role": "admin"}
        )
    ).status_code == 403


async def test_stale_evidence_and_revision(support_context):
    client, workspace, path, ticket = await prepared(support_context)
    assert (
        await client.post(
            path + "/review", json=review_body(ticket, expected_revision=99)
        )
    ).status_code == 409
    await client.delete(
        f"/api/v1/workspaces/{workspace}/documents/"
        + ticket["sources"][0]["document_id"]
    )
    assert (
        await client.post(path + "/review", json=review_body(ticket))
    ).status_code == 409
    client2, _, path2, ticket2 = await prepared(support_context)
    async with support_context.factory() as db:
        order = await db.scalar(
            select(DemoOrder).where(
                DemoOrder.id == ticket2["proposed_action"]["order_id"]
            )
        )
        order.refundable = False
        await db.commit()
    assert (
        await client2.post(path2 + "/review", json=review_body(ticket2))
    ).status_code == 409


async def test_rejection_and_feedback(support_context):
    client, workspace, path, ticket = await prepared(support_context)
    response = await client.post(
        path + "/review", json=review_body(ticket, decision="reject")
    )
    assert (
        response.status_code == 200
        and response.json()["ticket"]["status"] == "rejected"
    )
    for vote in [-1, 1]:
        assert (
            await client.post(path + "/feedback", json={"vote": vote})
        ).status_code == 200
    snapshot = (await client.get(f"/api/v1/workspaces/{workspace}/overview")).json()
    assert snapshot["metrics"]["feedback"] == {"1": 1}
    assert any(e["event"] == "review.rejected" for e in snapshot["audit"])
    assert (
        next(o for o in snapshot["orders"] if o["reference"] == "ORD-1003")["status"]
        == "delivered"
    )


async def test_document_queue_and_knowledge_history(support_context):
    client = await support_context.account()
    workspace = await create_workspace(client)
    prefix = f"/api/v1/workspaces/{workspace}"
    response = await client.post(
        prefix + "/documents",
        files={
            "file": (
                "shipping.md",
                b"Shipping takes five business days. Tracking is available after shipment.",
                "text/markdown",
            )
        },
    )
    assert response.status_code == 202, response.text
    document = response.json()
    assert document["status"] == "queued"
    assert (
        await client.post(prefix + "/ask", json={"question": "Shipping timeline?"})
    ).json()["sources"] == []
    assert await process_once(support_context.factory)
    answer = (
        await client.post(prefix + "/ask", json={"question": "Shipping timeline?"})
    ).json()
    assert (
        answer["sources"][0]["document_id"] == document["id"]
        and "five business days" in answer["answer"]
    )
    await client.delete(prefix + f"/documents/{document['id']}")
    assert (
        await client.post(prefix + "/ask", json={"question": "Shipping timeline?"})
    ).json()["sources"] == []
    assert await process_once(support_context.factory)
    snapshot = (await client.get(prefix + "/overview")).json()
    assert snapshot["documents"] == [] and len(snapshot["answers"]) == 3


async def test_failed_document_retry_budget(support_context):
    client = await support_context.account()
    workspace = await create_workspace(client)
    prefix = f"/api/v1/workspaces/{workspace}"
    response = await client.post(
        prefix + "/documents",
        files={"file": ("broken.pdf", b"%PDF-1.7\nbroken", "application/pdf")},
    )
    assert response.status_code == 202
    for attempt in range(1, 4):
        assert await process_once(support_context.factory)
        document = (await client.get(prefix + "/overview")).json()["documents"][0]
        assert document["status"] == "failed" and document["attempts"] == attempt
        assert (
            await client.post(prefix + f"/documents/{document['id']}/retry")
        ).status_code == (200 if attempt < 3 else 409)
    assert not await process_once(support_context.factory)


async def test_customer_mismatch(support_context):
    client = await support_context.account()
    workspace = await create_workspace(client)
    await demo(support_context, client, workspace)
    prefix = f"/api/v1/workspaces/{workspace}"
    ticket = (
        await client.post(
            prefix + "/tickets",
            json={
                "subject": "Refund order",
                "description": "Refund order ORD-1003",
                "customer_email": "different@example.com",
                "order_reference": "ORD-1003",
            },
        )
    ).json()
    response = await prepare_draft(
        support_context, client, prefix + f"/tickets/{ticket['id']}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["tool_calls"][0]["result"] == {"found": False}
    assert response.json()["proposed_action"]["kind"] == "escalate"


async def test_csrf_validation_and_seed_idempotency(support_context):
    client = await support_context.account()
    workspace = await create_workspace(client)
    prefix = f"/api/v1/workspaces/{workspace}"
    token = client.headers.pop("X-CSRF-Token")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 403
    client.headers["X-CSRF-Token"] = token
    await demo(support_context, client, workspace)
    snapshot = await demo(support_context, client, workspace)
    assert (
        len(snapshot["orders"]) == 3
        and len(snapshot["tickets"]) == 3
        and len(snapshot["documents"]) == 2
    )
    assert (
        await client.post(
            prefix + "/documents",
            files={"file": ("bad.exe", b"x", "application/octet-stream")},
        )
    ).status_code == 422
    assert (
        await client.post(
            prefix + "/tickets",
            json={"subject": " ", "description": "x", "customer_email": "bad"},
        )
    ).status_code == 422


async def test_rate_budget_and_workflow_failure(support_context):
    client = await support_context.account()
    workspace = await create_workspace(client)
    for _ in range(10):
        assert (
            await client.post(
                f"/api/v1/workspaces/{workspace}/ask",
                json={"question": "Any policy?"},
            )
        ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/workspaces/{workspace}/ask", json={"question": "Any policy?"}
        )
    ).status_code == 429
    client2, _, path2, _ = await prepared(support_context)
    with patch(
        "resolveai.workflow.SupportEngine.graph",
        new=AsyncMock(side_effect=RuntimeError("provider down")),
    ):
        assert (await prepare_draft(support_context, client2, path2)).status_code == 200
    ticket = (await client2.get(path2)).json()
    assert (
        ticket["status"] == "open"
        and ticket["last_error"]
        and ticket["runs"][0]["status"] == "failed"
    )


def test_triage_evaluation_set():
    for message, category, action in [
        ("Where is my order?", "shipping", "reply"),
        ("Please refund my order", "refund", "refund"),
        ("My lamp arrived damaged", "replacement", "replacement"),
        ("I was charged twice", "billing", "reply"),
        ("How do I update my profile?", "general", "reply"),
    ]:
        result = local_triage(message, message, None)
        assert result.category == category and result.requested_action == action


async def test_two_tickets_cannot_refund_same_order_twice(support_context):
    client, workspace, path, ticket = await prepared(support_context)
    prefix = f"/api/v1/workspaces/{workspace}"
    other = (
        await client.post(
            prefix + "/tickets",
            json={
                "subject": "Second refund request",
                "description": "Refund the same order ORD-1003",
                "customer_email": "nora@example.com",
                "order_reference": "ORD-1003",
            },
        )
    ).json()
    other_path = prefix + f"/tickets/{other['id']}"
    draft = await prepare_draft(support_context, client, other_path)
    assert draft.status_code == 200
    results = await asyncio.gather(
        client.post(path + "/review", json=review_body(ticket)),
        client.post(other_path + "/review", json=review_body(draft.json())),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    async with support_context.factory() as db:
        assert await db.scalar(select(func.count()).select_from(SupportDecision)) == 1


async def test_worker_reclaims_interrupted_document(support_context):
    from datetime import datetime, timedelta, timezone

    from database.models import BusinessChunk, BusinessDocument

    client = await support_context.account()
    workspace = await create_workspace(client)
    prefix = f"/api/v1/workspaces/{workspace}"
    upload = await client.post(
        prefix + "/documents",
        files={"file": ("policy.md", b"Shipping takes five days.", "text/markdown")},
    )
    document_id = upload.json()["id"]
    async with support_context.factory() as db:
        document = await db.get(BusinessDocument, document_id)
        document.status, document.attempts, document.lease_id = (
            "processing",
            1,
            str(uuid4()),
        )
        document.updated_at = datetime.now(timezone.utc) - timedelta(minutes=13)
        await db.commit()
    assert await process_once(support_context.factory)
    document = (await client.get(prefix + "/overview")).json()["documents"][0]
    assert document["status"] == "ready" and document["attempts"] == 2
    async with support_context.factory() as db:
        assert await db.scalar(select(func.count()).select_from(BusinessChunk)) == 1
        assert (await db.get(BusinessDocument, document_id)).content is None


async def test_local_mode_never_calls_paid_providers(support_context):
    with patch(
        "ai.gemini.genai.Client",
        side_effect=AssertionError("Unexpected model call"),
    ):
        client, workspace, _, _ = await prepared(support_context)
        result = await client.post(
            f"/api/v1/workspaces/{workspace}/ask",
            json={"question": "What is our refund policy?"},
        )
        assert result.status_code == 200


async def test_live_mode_requires_configuration(support_context, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = await support_context.account()
    response = await client.post(
        "/api/v1/workspaces", json={"name": "Live workspace", "ai_mode": "gemini"}
    )
    assert (
        response.status_code == 503
        and "GEMINI_API_KEY" in response.json()["error"]["message"]
    )
    assert (await client.get("/api/v1/workspaces")).json() == []


async def test_invalid_model_citations_are_rejected():
    from fastapi import HTTPException

    from resolveai.schemas import Draft
    from resolveai.workflow import SupportEngine

    engine = SupportEngine("gemini", None, lambda: "test-model", None)
    engine.structured = AsyncMock(
        return_value=Draft(
            reply="Invented policy [99]", source_ids=[1], rationale="test"
        )
    )
    try:
        await engine.answer("Refund policy?", [{"id": 1, "text": "Known policy"}])
    except HTTPException as error:
        assert error.status_code == 502
    else:
        raise AssertionError("Unsupported citations must not be returned")


async def test_pgvector_retrieval_filters_workspace_status_and_similarity(
    support_context,
):
    from database.models import BusinessChunk, BusinessDocument
    from resolveai.knowledge import retrieve

    client = await support_context.account()
    workspace = await create_workspace(client)
    snapshot = await demo(support_context, client, workspace)
    wanted = snapshot["documents"][0]["id"]
    other = await create_workspace(client)
    await demo(support_context, client, other)
    query_vector = [1.0] + [0.0] * 767
    async with support_context.factory() as db:
        for chunk in (await db.scalars(select(BusinessChunk))).all():
            chunk.embedding = (
                query_vector
                if str(chunk.document_id) == wanted or str(chunk.workspace_id) == other
                else [-1.0] + [0.0] * 767
            )
        await db.commit()
        with patch(
            "resolveai.knowledge.embed_texts", AsyncMock(return_value=[query_vector])
        ) as embed:
            result = await retrieve(db, UUID(workspace), "refund", "gemini")
            assert result and all(x["document_id"] == wanted for x in result)
            assert all(x["score"] >= 0.35 for x in result)
            embed.assert_awaited_once()
        document = await db.get(BusinessDocument, wanted)
        document.status = "deleting"
        await db.commit()
        with patch(
            "resolveai.knowledge.embed_texts", AsyncMock(return_value=[query_vector])
        ):
            assert await retrieve(db, UUID(workspace), "refund", "gemini") == []
