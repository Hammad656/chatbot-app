"""Integration tests use real PostgreSQL and a disposable random schema."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base
from database.session import POSTGRES_URL, get_db
from main import app


@pytest.fixture
async def support_context(monkeypatch, tmp_path):
    monkeypatch.setenv("RESOLVEAI_INLINE_WORKER", "false")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("JWT_SECRET_KEY", "temporary-integration-test-signing-key")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("POSTGRES_PASSWORD", "nonempty-test-configuration-guard")
    schema = "resolveai_test_" + uuid4().hex
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL,
        execution_options={"schema_translate_map": {None: schema}},
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    clients = []

    async def account(email=None):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        clients.append(client)
        client.headers["X-CSRF-Token"] = (await client.get("/api/v1/auth/csrf")).json()[
            "csrf_token"
        ]
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": email or f"test-{uuid4().hex}@example.com",
                "password": "temporary-test-password-123",
            },
        )
        assert response.status_code == 201, response.text
        return client

    try:
        yield SimpleNamespace(account=account, factory=factory)
    finally:
        app.dependency_overrides.pop(get_db, None)
        for client in clients:
            await client.aclose()
        await engine.dispose()
        async with admin.begin() as connection:
            assert schema.startswith("resolveai_test_")
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
