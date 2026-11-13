# ResolveAI

One company-knowledge and support application at **http://localhost:3000**.
Upload company documents in **Knowledge base**, then chat with cited answers in
the same dashboard. The personal CV chatbot and its APIs have been retired.
Existing accounts, company documents and support records are preserved.

The local stack is Next.js, FastAPI, a separate Redis/arq worker, PostgreSQL 17
with pgvector, and private document storage. The browser uses relative
`/api/v1` requests; Next.js proxies them to the internal API. Only the web
service publishes a port. The old `/resolve` address redirects to the company dashboard.

## Start or update

Keep your existing private `backend/.env`, including your PostgreSQL password,
JWT signing key, and Gemini API key. `JWT_SECRET_KEY` must be a random value of
at least 32 characters, not a placeholder. Use `COOKIE_SECURE=false` for local
HTTP and `true` when deploying behind HTTPS. From the project root:

```powershell
git switch dev
git pull --ff-only origin dev
docker compose --env-file backend/.env up --build --force-recreate -d
docker compose --env-file backend/.env ps
```

The API applies additive migrations, provisions non-owner database roles and
starts serving. The worker handles document ingestion and ticket preparation.
The PostgreSQL image stays on major version 17, so the existing named database
volume can be reused. Original uploads now use a private shared volume.

## Authentication and workspaces

- **Sign in:** http://localhost:3000/login
- **Create account:** http://localhost:3000/signup
- **Company knowledge chat:** http://localhost:3000/knowledge
- **API reference:** http://localhost:3000/docs
- **API schema:** http://localhost:3000/api/v1/openapi.json

New accounts go through `/onboarding` to create their first workspace. The
creator is its **owner**. Owners and admins can invite agents or admins from
**Workspace team**, change roles, revoke invitations and remove members.
Invitation links can be accepted after the invited person signs up; no email
service or new API key is required. Settings belong to the active workspace.

Access JWTs last 15 minutes, stay in browser memory, and carry the active
workspace and role. The 30-day opaque refresh cookie is httpOnly; refresh
values are stored hashed, rotated on use, and a replay revokes the family.
A workspace switch checks membership and issues a new access token. Every
business request checks current membership and uses PostgreSQL RLS. Agent
accounts cannot manage members, inspect admin-only orders/audit data, or
approve sensitive mock actions.

**After this upgrade, sign in again once.** Existing accounts/passwords,
company documents, tickets and workspaces remain. Old week-long cookie sessions
are revoked, and existing workspace creators are promoted to owner.

See the [authentication and tenancy learning guide](docs/AUTH_AND_TENANCY.md)
for files, endpoint examples, browser behavior and troubleshooting.

## Pinecone and embeddings

The supplied system design chooses **PostgreSQL + pgvector**, so this version
does not use Pinecone. You do not need a new Pinecone index, Pinecone embedding
model, or Pinecone key. Existing Pinecone data is left untouched. Once you have
updated successfully, `PINECONE_API_KEY` and `PINECONE_INDEX_NAME` can be removed
from `backend/.env`; leaving them there does not activate a Pinecone connection.

Keep `GEMINI_API_KEY` for Gemini-mode chat and embeddings. New workspaces use
`EMBEDDING_MODEL=gemini-embedding-001` by default and a fixed vector dimension
of 768. A workspace keeps its embedding model so incompatible vectors cannot
silently be mixed. The HNSW cosine index is created by migration, not manually.

Existing Gemini company documents are queued for embedding from their stored
text during migration. Wait for **Ready**. You do not need to upload them again
when that text remains available. A new upload is parsed, chunked, embedded in
batches, and stored automatically. Gemini calls use your provider quota.
Local demo mode uses keyword retrieval and makes no provider requests.

Check generation and embedding access from the running API container:

```powershell
docker compose --env-file backend/.env exec api python check_gemini.py --structured --embeddings
```

## Documentation

- [Learning and workflow guide](docs/RESOLVEAI.md)
- [System-design implementation map and remaining differences](docs/architecture.md)
- [Original supplied system design](docs/design/ResolveAI_System_Design.pdf)
- [Uploadable fictional company documents and test guide](docs/demo/README.md)

The broader design still includes a public customer widget,
streaming, detailed model-cost accounting and production cloud integrations.
The implementation map distinguishes those items from the company-dashboard
workflow delivered here; this repository does not claim a complete production
rollout or the design's unmeasured latency/evaluation targets.

## Checks

Backend integration tests use real PostgreSQL/pgvector with disposable schemas.
The browser tests in `frontend/e2e` exercise auth, refresh across tabs, account
and workspace isolation, the actual ingestion worker, cited chat and invitations.
They use Local demo mode and spend no model credits. GitHub Actions runs backend,
frontend and browser checks against an isolated Compose stack.

To run browser checks against your running **development** stack:

```powershell
cd frontend
npm ci
npx playwright install chromium
npm run test:e2e
```

The browser checks create synthetic accounts and workspaces.
