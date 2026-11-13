# ResolveAI backend

Python 3.12, FastAPI, async SQLAlchemy, PostgreSQL with pgvector and a Redis/arq
worker. All active product routes use `/api/v1`. There is one company knowledge
pipeline; the old `/api/cv`, `/api/chats`, and `/api/chat/...` APIs are removed.
Historical personal-chat tables remain archived for data preservation and are
not granted to the new runtime roles.

Use the [root setup instructions](../README.md) for Docker. In the browser,
OpenAPI documentation is available at **http://localhost:3000/api/v1/docs**.
`/health` is a private-container liveness endpoint; `/api/v1/health/ready` checks
the migrated schema, vector extension, and runtime role.

## Configuration

Keep your existing private `.env`. Required settings are PostgreSQL credentials
and `JWT_SECRET_KEY`. Gemini mode also requires `GEMINI_API_KEY`; `GEMINI_MODEL`
defaults to `gemini-flash-latest`. New workspaces use `EMBEDDING_MODEL`, default
`gemini-embedding-001`. Vector dimension 768 is fixed in the migration.
`RAG_MIN_SIMILARITY` defaults to 0.35. Pinecone settings are unused.

Compose supplies `REDIS_URL`, `UPLOAD_DIR`, internal database host and runtime
roles. `resolveai_app` handles API requests, and `resolveai_worker` handles jobs;
neither owns business tables or bypasses RLS. Migrations run with the original
owner credentials. Set distinct `RESOLVEAI_APP_PASSWORD` and
`RESOLVEAI_WORKER_PASSWORD` for a non-local deployment; local Compose can reuse
the existing database password. Migration/worker credentials are removed from
the API process environment after bootstrap.

For standalone development, install `requirements-dev.txt`, use a pgvector-
enabled PostgreSQL server and Redis, apply Alembic migrations as the owner,
then run the API with runtime-role credentials. Run the worker separately:

```powershell
uvicorn main:app --reload
arq resolveai.jobs.WorkerSettings
```

Those commands belong in separate terminals only for standalone development.
Docker Compose runs both processes for you. Do not combine an inline worker
with the dedicated Docker worker; Compose sets `RESOLVEAI_INLINE_WORKER=false`.

## AI diagnostics

From the project root:

```powershell
docker compose --env-file backend/.env exec api python check_gemini.py --structured --embeddings
docker compose --env-file backend/.env logs --tail 80 api worker
```

The checks use synthetic data, consume provider quota, and do not send an
uploaded company document. Errors include a bounded key-redacted provider
diagnostic in server logs. Model listing alone does not establish generation or
embedding access. Updating `.env` requires container recreation via Compose.

The shared AI boundary strips Gemini's unsupported `additionalProperties`
schema field while preserving strict Pydantic validation on returned output.
Invalid structured output gets one repair request; unauthorized fields and
unsupported citations remain blocked.

## Verification

Use a development/CI database with the vector extension and a role able to
create disposable test schemas:

```powershell
python -m alembic upgrade head
python -m alembic check
python -m pytest -q
```

Integration tests use separate random schemas, explicit schema translation,
and real PostgreSQL/pgvector. They cover company chat threads, tenant-scoped
retrieval, non-owner RLS, queue preparation, authorization, approval and
idempotent mock actions. Provider transport tests use the real Gemini SDK with
an in-memory HTTP transport. They do not spend provider credits.
