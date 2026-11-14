import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text

from database.models import (
    ActionRequest,
    CustomerMessage,
    CustomerSession,
    CustomerTurn,
    DemoOrder,
)
from customers.worker import process_customer_turn_once
from resolveai.actions import process_action_once
from resolveai.tickets import process_ticket_once
from test_support import create_workspace, demo, review_body


async def setup(context, email="alex@example.com"):
    owner = await context.account()
    workspace = await create_workspace(owner)
    await demo(context, owner, workspace)
    settings = await owner.patch(
        "/api/v1/workspace/settings", json={"settings": {"widget_enabled": True}}
    )
    assert settings.status_code == 200, settings.text
    key = settings.json()["widget_public_key"]
    access = await owner.post(
        "/api/v1/customer-access-links", json={"email": email, "name": "Test customer"}
    )
    assert access.status_code == 201, access.text
    return owner, workspace, key, access.json()


async def customer(context, key, code=None):
    client = await context.account()
    client.headers.pop("Authorization")
    response = await client.post(
        "/api/v1/widget/session", json={"workspace_key": key, "access_token": code}
    )
    assert response.status_code == 200, response.text
    session = response.json()
    client.headers["Authorization"] = "Bearer " + session["access_token"]
    return client, session


async def message(context, client, session, content, request_id=None):
    request_id = request_id or uuid4()
    task = asyncio.create_task(
        client.post(
            "/api/v1/widget/messages",
            json={"content": content, "request_id": str(request_id)},
        )
    )
    turn = None
    for _ in range(120):
        async with context.factory() as db:
            turn = await db.scalar(
                select(CustomerTurn).where(CustomerTurn.request_id == request_id)
            )
        if turn:
            break
        if task.done():
            response = task.result()
            raise AssertionError(response.text)
        await asyncio.sleep(0.025)
    assert turn
    await process_customer_turn_once(
        workspace_id=turn.workspace_id, turn_id=turn.id, factory=context.factory
    )
    response = await asyncio.wait_for(task, 8)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    async with context.factory() as db:
        turn = await db.get(CustomerTurn, turn.id)
    return response, turn


async def prepare(context, owner, turn):
    ticket = (await owner.get(f"/api/v1/tickets/{turn.ticket_id}")).json()
    run_id = UUID(ticket["runs"][0]["id"])
    assert await process_ticket_once(
        workspace_id=turn.workspace_id, run_id=run_id, factory=context.factory
    )
    response = await owner.get(f"/api/v1/tickets/{turn.ticket_id}")
    assert response.json()["status"] == "awaiting_approval", response.text
    return response.json()


async def test_customer_order_reaches_review_then_delivers_once(support_context):
    owner, workspace, key, link = await setup(support_context)
    client, session = await customer(support_context, key, link["access_token"])
    assert session["verified"] is True and session["role"] == "customer"
    response, turn = await message(
        support_context, client, session, "Where is my order ORD-1001?"
    )
    assert "event: ticket" in response.text and turn.status == "awaiting_review"
    ticket = await prepare(support_context, owner, turn)
    assert (
        ticket["category"] == "shipping"
        and ticket["tool_calls"][0]["result"]["status"] == "shipped"
    )
    path = f"/api/v1/widget/conversations/{session['conversation_id']}"
    assert "shipped" not in (await client.get(path)).text
    reviewed = await owner.post(
        f"/api/v1/tickets/{turn.ticket_id}/review", json=review_body(ticket)
    )
    assert (
        reviewed.status_code == 200
        and reviewed.json()["ticket"]["status"] == "delivery_pending"
    )
    assert "shipped" not in (await client.get(path)).text
    async with support_context.factory() as db:
        action = await db.scalar(
            select(ActionRequest).where(ActionRequest.ticket_id == turn.ticket_id)
        )
    assert await process_action_once(
        workspace_id=UUID(workspace),
        action_id=action.id,
        factory=support_context.factory,
    )
    assert not await process_action_once(
        workspace_id=UUID(workspace),
        action_id=action.id,
        factory=support_context.factory,
    )
    history = (await client.get(path)).json()
    assert (
        history["messages"][-1]["role"] == "agent"
        and "shipped" in history["messages"][-1]["content"]
    )
    feedback = await client.post(
        "/api/v1/widget/feedback",
        json={"message_id": history["messages"][-1]["id"], "vote": 1},
    )
    assert feedback.status_code == 200, feedback.text
    staff_history = await owner.get(
        f"/api/v1/customer-conversations/{session['conversation_id']}"
    )
    assert staff_history.json()["contact"]["email"] == "alex@example.com"
    assert (await owner.get("/api/v1/actions?status=succeeded")).json()["items"][0][
        "id"
    ] == str(action.id)
    async with support_context.factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(CustomerMessage)
                .where(CustomerMessage.delivery_id == action.id)
            )
            == 1
        )


async def test_guest_identity_cannot_claim_orders_and_customer_tokens_cannot_use_staff_api(
    support_context,
):
    owner, _, key, link = await setup(support_context)
    guest, session = await customer(support_context, key)
    assert session["verified"] is False
    _, turn = await message(
        support_context, guest, session, "Where is my order ORD-1001?"
    )
    assert turn.ticket_id is None
    history = (
        await guest.get(f"/api/v1/widget/conversations/{session['conversation_id']}")
    ).json()
    assert "private customer access link" in history["messages"][-1]["content"]
    assert "shipped" not in str(history)
    assert (await guest.get("/api/v1/tickets")).status_code == 401
    verified, identity = await customer(support_context, key, link["access_token"])
    assert (
        await guest.get(f"/api/v1/widget/conversations/{identity['conversation_id']}")
    ).status_code == 404
    assert (
        await verified.get(f"/api/v1/widget/conversations/{session['conversation_id']}")
    ).status_code == 404
    assert (
        await owner.post(
            "/api/v1/widget/messages",
            json={"content": "hello", "request_id": str(uuid4())},
        )
    ).status_code == 401


async def test_customer_knowledge_stream_is_cited_and_unknown_questions_escalate(
    support_context,
):
    owner, _, key, _ = await setup(support_context)
    client, session = await customer(support_context, key)
    response, turn = await message(
        support_context, client, session, "What is your shipping policy?"
    )
    assert turn.status == "completed" and "event: delta" in response.text
    history = (
        await client.get(f"/api/v1/widget/conversations/{session['conversation_id']}")
    ).json()
    assert (
        history["messages"][-1]["sources"]
        and "[1]" in history["messages"][-1]["content"]
    )
    _, escalated = await message(
        support_context, client, session, "I would like to speak to a human"
    )
    assert escalated.ticket_id and escalated.status == "awaiting_review"
    assert (
        await owner.get(f"/api/v1/tickets/{escalated.ticket_id}")
    ).status_code == 200


async def test_order_questions_request_a_reference_and_budget_exhaustion_finishes_cleanly(
    support_context,
):
    from database.models import SupportRun, SupportTicket

    owner, workspace, key, link = await setup(support_context)
    client, session = await customer(support_context, key, link["access_token"])
    _, clarification = await message(
        support_context, client, session, "Where is my order?"
    )
    assert clarification.status == "completed" and clarification.ticket_id is None
    history = (
        await client.get(f"/api/v1/widget/conversations/{session['conversation_id']}")
    ).json()
    assert "share your order reference" in history["messages"][-1]["content"]
    # One turn is already recorded. Reach nine workflow requests, so routing can
    # begin but a second reserved run for the support handoff cannot be admitted.
    for _ in range(8):
        response = await owner.post(
            "/api/v1/knowledge/ask", json={"question": "What is your shipping policy?"}
        )
        assert response.status_code == 200, response.text
    _, exhausted = await message(
        support_context, client, session, "Where is my order ORD-1001?"
    )
    assert exhausted.status == "failed"
    assert (
        await client.get(f"/api/v1/widget/conversations/{session['conversation_id']}")
    ).json()["processing"] is False
    async with support_context.factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(SupportTicket)
                .where(SupportTicket.workspace_id == UUID(workspace))
            )
            == 3
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(SupportRun)
                .where(
                    SupportRun.workspace_id == UUID(workspace),
                    SupportRun.status == "running",
                )
            )
            == 0
        )


async def test_customer_logout_expiry_origin_and_revocation(support_context):
    owner, _, key, link = await setup(support_context)
    client, session = await customer(support_context, key, link["access_token"])
    assert (
        await client.post(
            "/api/v1/widget/session",
            json={"workspace_key": key, "site_origin": "https://unapproved.example"},
        )
    ).status_code == 403
    assert (
        await owner.delete(f"/api/v1/customer-access-links/{link['id']}")
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/widget/conversations/{session['conversation_id']}")
    ).status_code == 401
    other, identity = await customer(support_context, key)
    assert (await other.post("/api/v1/widget/logout")).status_code == 204
    assert (
        await other.get(f"/api/v1/widget/conversations/{identity['conversation_id']}")
    ).status_code == 401
    expired, identity = await customer(support_context, key)
    async with support_context.factory() as db:
        record = await db.scalar(
            select(CustomerSession).where(
                CustomerSession.conversation_id == UUID(identity["conversation_id"])
            )
        )
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    assert (
        await expired.get(f"/api/v1/widget/conversations/{identity['conversation_id']}")
    ).status_code == 401


async def test_authorized_external_order_adapter_enforces_workspace_and_customer(
    support_context, monkeypatch
):
    from resolveai.orders import lookup_order

    owner, workspace, _, _ = await setup(support_context)
    monkeypatch.setenv("ORDER_API_BASE_URL", "https://orders.test/api")
    monkeypatch.setenv("ORDER_API_TOKEN", "synthetic-provider-key")
    updated = await owner.patch(
        "/api/v1/workspace/settings", json={"settings": {"order_provider": "external"}}
    )
    assert updated.status_code == 200, updated.text

    def handler(request):
        assert request.headers["Authorization"] == "Bearer synthetic-provider-key"
        assert request.headers["X-Workspace-ID"] == workspace
        return httpx.Response(
            200,
            json={
                "reference": "ORD-EXT1",
                "customer_email": "alex@example.com",
                "status": "in_transit",
                "item": "Verified provider item",
                "amount_cents": 1200,
                "currency": "USD",
            },
        )

    original = httpx.AsyncClient
    with patch(
        "resolveai.orders.httpx.AsyncClient",
        side_effect=lambda **kwargs: original(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    ):
        async with support_context.factory() as db:
            result = await lookup_order(
                db, UUID(workspace), "alex@example.com", "ORD-EXT1"
            )
            assert (
                result["found"]
                and result["status"] == "in_transit"
                and result["provider"] == "external"
            )
            assert await lookup_order(
                db, UUID(workspace), "someone-else@example.com", "ORD-EXT1"
            ) == {"found": False}
    async with support_context.factory() as db:
        assert (
            await db.scalar(
                select(DemoOrder.status).where(
                    DemoOrder.workspace_id == UUID(workspace),
                    DemoOrder.reference == "ORD-1001",
                )
            )
            == "shipped"
        )


async def test_customer_rls_blocks_other_conversations_and_unreviewed_business_data(
    support_context,
):
    from database.models import CustomerConversation, SupportTicket
    from database.security_v3 import install_security
    from database.tenant import set_customer

    _, workspace, key, _ = await setup(support_context)
    a, sa = await customer(support_context, key)
    _, sb = await customer(support_context, key)
    await message(support_context, a, sa, "What is your shipping policy?")
    role = "resolveai_customer_check_" + uuid4().hex
    async with support_context.factory() as owner:
        schema = await owner.scalar(text("SELECT current_schema()"))
        await (await owner.connection()).run_sync(lambda c: install_security(c, schema))
        await owner.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        await owner.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
        await owner.execute(
            text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}")
        )
        await owner.execute(
            text(
                f"GRANT EXECUTE ON FUNCTION {schema}.resolveai_customer_session(uuid,uuid,uuid),{schema}.resolveai_customer_contact(uuid) TO {role}"
            )
        )
        cs = await owner.scalar(
            select(CustomerSession).where(
                CustomerSession.conversation_id == UUID(sa["conversation_id"])
            )
        )
        sid = cs.id
        await owner.commit()
    try:
        async with support_context.factory() as restricted:
            await set_customer(
                restricted, UUID(workspace), sid, UUID(sa["conversation_id"])
            )
            await restricted.execute(text(f"SET LOCAL ROLE {role}"))
            records = (await restricted.scalars(select(CustomerConversation))).all()
            assert [str(record.id) for record in records] == [sa["conversation_id"]]
            assert (await restricted.scalars(select(CustomerMessage))).all()
            assert (await restricted.scalars(select(SupportTicket))).all() == []
            assert (await restricted.scalars(select(ActionRequest))).all() == []
            await set_customer(
                restricted, UUID(workspace), sid, UUID(sb["conversation_id"])
            )
            assert (await restricted.scalars(select(CustomerConversation))).all() == []
            assert (await restricted.scalars(select(CustomerMessage))).all() == []
            await restricted.rollback()
    finally:
        async with support_context.factory() as owner:
            await owner.execute(text(f"DROP OWNED BY {role}"))
            await owner.execute(text(f"DROP ROLE {role}"))
            await owner.commit()


async def test_delivery_rechecks_approver_membership(support_context):
    owner, workspace, key, link = await setup(support_context)
    client, session = await customer(support_context, key, link["access_token"])
    _, turn = await message(
        support_context, client, session, "Where is my order ORD-1001?"
    )
    ticket = await prepare(support_context, owner, turn)
    agent = await support_context.account("delivery-agent@example.com")
    assert (
        await owner.post(
            f"/api/v1/workspaces/{workspace}/members",
            json={"email": "delivery-agent@example.com", "role": "agent"},
        )
    ).status_code == 201
    await support_context.activate(agent, workspace)
    approved = await agent.post(
        f"/api/v1/tickets/{turn.ticket_id}/review", json=review_body(ticket)
    )
    assert approved.status_code == 200, approved.text
    people = (await owner.get("/api/v1/members")).json()
    member = next(
        person for person in people if person["email"] == "delivery-agent@example.com"
    )
    assert (await owner.delete(f"/api/v1/members/{member['id']}")).status_code == 204
    async with support_context.factory() as db:
        action = await db.scalar(
            select(ActionRequest).where(ActionRequest.ticket_id == turn.ticket_id)
        )
    await process_action_once(
        workspace_id=UUID(workspace),
        action_id=action.id,
        factory=support_context.factory,
    )
    history = (
        await client.get(f"/api/v1/widget/conversations/{session['conversation_id']}")
    ).json()
    assert not any(message["role"] == "agent" for message in history["messages"])
    failed = (await owner.get("/api/v1/actions?status=failed")).json()["items"]
    assert len(failed) == 1 and "no longer has permission" in failed[0]["error"]
