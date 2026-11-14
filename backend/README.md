# ResolveAI backend

Python 3.12, FastAPI, async SQLAlchemy, PostgreSQL with pgvector and a Redis/arq
worker. All active product routes use `/api/v1`. There is one company knowledge
pipeline; the old `/api/cv`, `/api/chats`, and `/api/chat/...` APIs are removed.
Historical personal-chat tables remain archived for data preservation and are
not granted to the new runtime roles.

Use the [root setup instructions](../README.md) for Docker. In the browser,
OpenAPI documentation is available at **http://localhost:3000/docs** (also `/api/v1/docs`).
`/health` is a private-container liveness endpoint; `/api/v1/health/ready` checks
the migrated schema, vector extension, and runtime role.

## Configuration

Keep your existing private `.env`. Required settings are PostgreSQL credentials
and a random `JWT_SECRET_KEY` of at least 32 characters. Startup validates it
without printing it. Gemini mode also requires `GEMINI_API_KEY`; `GEMINI_MODEL`
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

## Auth and tenant context

See [the auth/tenancy guide](../docs/AUTH_AND_TENANCY.md). `auth/` owns signup,
login, access-token validation, refresh rotation/reuse detection and logout.
`workspaces/` owns onboarding, active workspace switching, settings, invitations
and membership roles. `database/repository.py` scopes canonical resource APIs
from the authenticated principal, never a body/query workspace ID.

Authentication endpoints are `POST /api/v1/auth/signup`, `/login`, `/refresh`,
`/logout`, and `GET /api/v1/auth/me`. Access uses `Authorization: Bearer ...`.
Cookie-changing auth requests require the CSRF token from `GET /api/v1/auth/csrf`.
The API's OpenAPI schema exposes the bearer scheme and CSRF header. After
creating a workspace, call `POST /api/v1/workspaces/{id}/switch` and use the
returned access token before accessing tenant resources.

The canonical API provides `/workspace/settings`, `/members`, `/members/invite`,
`/invitations`, `/documents`, `/knowledge/search`, `/knowledge/ask`, `/tickets`,
`/conversations`, `/mock-orders`, `/analytics/overview` and `/audit-logs` under
`/api/v1`. Resource lists have scoped cursors where implemented. Compatibility
workspace-prefixed routes require the URL's workspace to match the signed
active workspace. Owner/admin restrictions are applied in both API surfaces.

Tenant mutations take a transaction-scoped workspace advisory lock before
locking child rows, so workspace deletion and worker completion do not invert
parent/child lock order. Provider work for knowledge answers and ticket drafts
runs outside that mutation lock. A final access/state check precedes saving.
Worker cron removes expired refresh families daily through a narrow database
function; the worker does not receive unrestricted auth-table access.

The API role cannot update/delete audit rows. Account-security events are a
separate private global log and have no public list API. Workspace deletion
requires the owner's current role and exact workspace-name confirmation.

## Customer workflow and approved delivery

`customers/` adds one-hour conversation-bound customer sessions, staff-issued
private access links, SSE answers, feedback and governed escalation tickets.
`database/security_v3.py` protects customer rows independently of staff membership;
customers cannot read unapproved drafts, actions or other conversations.

`resolveai/actions.py` stores draft versions and action requests, queues approved
work and delivers the reviewed reply into the customer conversation exactly once.
The worker rechecks the approver, evidence, revision and sample-order eligibility.
`resolveai/orders.py` selects the built-in order service or a configured external
read-only HTTP provider. A URL/token is required for a live external integration;
server credentials are never returned to the browser. Sample financial actions
remain simulated. See [the workflow guide](../docs/CUSTOMER_WORKFLOW.md).

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
