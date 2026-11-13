"""The main application has one company-scoped knowledge/chat workflow."""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy import select, text

from database.models import BusinessChunk, BusinessDocument
from database.security import install_rls
from database.tenant import set_tenant
from resolveai.storage import path_for
from resolveai.worker import process_once


async def workspace(client, name="Company"):
    response = await client.post("/api/v1/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def document(context, client, workspace_id):
    prefix = f"/api/v1/workspaces/{workspace_id}"
    upload = await client.post(
        prefix + "/documents",
        files={
            "file": (
                "company.md",
                b"# Company returns\nUnopened products may be returned within 21 days. A person must approve refunds.",
                "text/markdown",
            )
        },
    )
    assert upload.status_code == 202, upload.text
    assert await process_once(context.factory)
    return upload.json()["id"]


async def test_conversations_follow_up_and_reindex_private_company_document(
    support_context,
):
    client = await support_context.account()
    company = await workspace(client)
    prefix = f"/api/v1/workspaces/{company}"
    doc_id = await document(support_context, client, company)
    first = await client.post(
        prefix + "/ask", json={"question": "What is our returns policy?"}
    )
    assert first.status_code == 200, first.text
    assert "21 days" in first.json()["answer"] and first.json()["sources"]
    thread = first.json()["thread_id"]
    second = await client.post(
        prefix + "/ask", json={"question": "Is approval needed?", "thread_id": thread}
    )
    assert second.status_code == 200 and second.json()["thread_id"] == thread
    history = await client.get(prefix + f"/threads/{thread}")
    assert len(history.json()) == 2
    new = await client.post(prefix + "/ask", json={"question": "What about refunds?"})
    assert new.json()["thread_id"] != thread
    async with support_context.factory() as db:
        stored = await db.get(BusinessDocument, UUID(doc_id))
        assert stored.content is None and path_for(stored.storage_key).is_file()
    assert (
        await client.post(prefix + f"/documents/{doc_id}/reindex")
    ).status_code == 200
    assert await process_once(support_context.factory)
    assert (await client.get(prefix + "/overview")).json()["documents"][0][
        "status"
    ] == "ready"
    assert (await client.get("/api/cv")).status_code == 404
    assert (await client.get("/api/chats")).status_code == 404


async def test_thread_and_sources_cannot_cross_workspace_boundaries(support_context):
    client = await support_context.account()
    outsider = await support_context.account()
    a, b = await workspace(client, "Company A"), await workspace(client, "Company B")
    await document(support_context, client, a)
    answer = (
        await client.post(
            f"/api/v1/workspaces/{a}/ask", json={"question": "Returns policy?"}
        )
    ).json()
    thread = answer["thread_id"]
    assert (
        await client.get(f"/api/v1/workspaces/{b}/threads/{thread}")
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/workspaces/{b}/ask",
            json={"question": "Refunds?", "thread_id": thread},
        )
    ).status_code == 404
    assert (
        await outsider.get(f"/api/v1/workspaces/{a}/threads/{thread}")
    ).status_code == 404
    empty = await client.post(
        f"/api/v1/workspaces/{b}/ask", json={"question": "Returns policy?"}
    )
    assert empty.status_code == 200 and not empty.json()["sources"]


async def test_gemini_workspace_needs_no_pinecone_and_empty_knowledge_is_free(
    support_context, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.delenv("PINECONE_INDEX_NAME", raising=False)
    client = await support_context.account()
    created = await client.post(
        "/api/v1/workspaces", json={"name": "Company semantic", "ai_mode": "gemini"}
    )
    assert created.status_code == 201
    with patch(
        "ai.gemini.genai.Client",
        side_effect=AssertionError("No provider calls without ready sources"),
    ):
        answer = await client.post(
            f"/api/v1/workspaces/{created.json()['id']}/ask",
            json={"question": "Company returns policy?"},
        )
    assert answer.status_code == 200 and not answer.json()["sources"]


async def test_old_document_text_can_be_embedded_without_reupload(support_context):
    from database.models import Workspace

    client = await support_context.account()
    company = await workspace(client)
    doc_id = await document(support_context, client, company)
    async with support_context.factory() as db:
        doc = await db.get(BusinessDocument, UUID(doc_id))
        doc.storage_key, doc.content, doc.status = None, None, "queued"
        (await db.get(Workspace, UUID(company))).ai_mode = "gemini"
        await db.commit()
    vector = [1.0] + [0.0] * 767
    with patch(
        "resolveai.worker.embed_texts", AsyncMock(return_value=[vector])
    ) as embed:
        assert await process_once(support_context.factory)
    embed.assert_awaited_once()
    async with support_context.factory() as db:
        doc = await db.get(BusinessDocument, UUID(doc_id))
        chunk = await db.scalar(
            select(BusinessChunk).where(BusinessChunk.document_id == doc.id)
        )
        assert doc.status == "ready" and len(chunk.embedding) == 768
        assert "21 days" in chunk.text


async def test_postgres_rls_blocks_an_unfiltered_query_and_wrong_scope(support_context):
    a, b = await support_context.account(), await support_context.account()
    wa, wb = await workspace(a, "Company A"), await workspace(b, "Company B")
    await document(support_context, a, wa)
    await document(support_context, b, wb)
    uid = (await a.get("/api/v1/auth/me")).json()["id"]
    role = "resolveai_check_" + uuid4().hex
    async with support_context.factory() as owner:
        schema = await owner.scalar(text("SELECT current_schema()"))
        connection = await owner.connection()
        await connection.run_sync(lambda c: install_rls(c, schema))
        await owner.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        await owner.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
        await owner.execute(
            text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}")
        )
        await owner.commit()
    try:
        async with support_context.factory() as restricted:
            await set_tenant(restricted, UUID(wa), UUID(uid))
            await restricted.execute(text(f"SET LOCAL ROLE {role}"))
            rows = (await restricted.scalars(select(BusinessChunk))).all()
            assert rows and all(str(row.workspace_id) == wa for row in rows)
            await set_tenant(restricted, UUID(wb), UUID(uid))
            assert (await restricted.scalars(select(BusinessChunk))).all() == []
            await restricted.rollback()
    finally:
        async with support_context.factory() as owner:
            await owner.execute(text(f"DROP OWNED BY {role}"))
            await owner.execute(text(f"DROP ROLE {role}"))
            await owner.commit()


async def test_knowledge_failure_recovers_an_aborted_transaction(
    support_context, monkeypatch
):
    from database.models import SupportRun

    client = await support_context.account()
    company = await workspace(client)

    async def fail(db, *args):
        await db.execute(text("SELECT 1/0"))

    monkeypatch.setattr("resolveai.router.retrieve", fail)
    response = await client.post(
        f"/api/v1/workspaces/{company}/ask", json={"question": "Company policy?"}
    )
    assert response.status_code == 503
    async with support_context.factory() as db:
        run = await db.scalar(select(SupportRun))
        assert run.status == "failed" and run.completed_at and run.error
