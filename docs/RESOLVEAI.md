# ResolveAI: workflow and learning guide

ResolveAI is the main application at **http://localhost:3000**. The personal CV
chatbot has been retired. Company knowledge chat, documents, support tickets,
mock orders, review controls, activity and team membership live in one dashboard.
The original `/resolve` bookmark redirects to `/knowledge`.

Read the [system-design implementation map](architecture.md) alongside this
guide. It records the exact differences between the requested dashboard
workflow and the full supplied production/MVP blueprint.

## 1. Start or upgrade

Keep your existing private `backend/.env` and PostgreSQL volume. With Docker
Desktop's engine running, execute from your project root on `dev`:

```powershell
git pull --ff-only origin dev
docker compose --env-file backend/.env up --build -d
docker compose --env-file backend/.env ps
```

The stack has web, API, worker, PostgreSQL/pgvector and Redis services. Only web
publishes port 3000. The API applies migrations and provisions non-owner roles
before its readiness check succeeds; the worker starts after that. Every
Compose command needs `--env-file backend/.env` for configuration interpolation.

```powershell
docker compose --env-file backend/.env logs --tail 80 api worker
docker compose --env-file backend/.env stop
docker compose --env-file backend/.env start
```

`stop` preserves containers and networks. Company documents and the database
use persistent named volumes. Windows shell-script line endings are normalized
during the backend image build.

## 2. Pinecone, pgvector and your existing data

The attached design explicitly chooses PostgreSQL with pgvector and no separate
vector database. This version follows that choice. `PINECONE_API_KEY` and
`PINECONE_INDEX_NAME` are unused and can be removed after upgrading successfully.
No replacement Pinecone index is required, and the application neither deletes
nor modifies the old Pinecone index.

Keep `GEMINI_API_KEY` for Gemini-mode generation and embeddings. The default
embedding model is `gemini-embedding-001`, with 768-dimensional vectors. A
workspace records its embedding model when created. Changing the environment
default affects new workspaces, not existing embeddings. A different model
requires a deliberate workspace-wide re-embedding migration; never mix models.

Migrations add the vector column, HNSW cosine index, document-storage metadata,
knowledge conversations and RLS policies. Existing accounts and support records
are preserved. Prior company answers are placed in a history conversation.
Existing Gemini documents are queued for embedding from stored chunks, so a
previously processed document normally does not need to be uploaded again.
Historical personal-chat tables remain archived and are not exposed by an API.

The database image remains PostgreSQL major version 17 while adding pgvector.
This preserves compatibility with the existing Compose database volume instead
of attempting a downgrade to the PDF's example version 16.

## 3. Company knowledge pipeline

1. An authenticated admin uploads a PDF, DOCX, UTF-8 TXT or Markdown file, up to
   20 MB. The API checks the extension/signature, computes a checksum, and rejects
   duplicate content within that workspace.
2. The file is stored under an opaque random key in a private shared volume;
   a document row is committed in PostgreSQL. Only then is its ID dispatched
   through Redis. Redis never needs the complete document body or provider key.
3. The arq worker reloads that workspace/document, establishes its transaction-
   local tenant context, claims a lease and parses text.
4. Markdown/DOCX headings become metadata. Text becomes overlapping approximately
   500-token passages using a 2,000-character limit and 240-character overlap.
   PDF page labels provide a fallback source location. Scanned PDFs require OCR
   before upload; this application does not include OCR.
5. In Gemini mode, the shared AI layer requests embeddings in batches of at most
   64 and normalizes the 768-dimensional vectors. Local mode skips this call.
6. Chunks and embeddings are replaced atomically in PostgreSQL; the document
   becomes Ready. The original private upload remains available for reindexing.
7. Failures retry with bounded backoff, up to three attempts. The dashboard shows
   failures and exposes retry/reindex controls. A periodic dispatcher recovers
   committed queued jobs if Redis was temporarily unavailable.

Parsing limits are 80 PDF pages, 500,000 extracted characters, 24 MB expanded
DOCX data and 500 passages per document. A workspace holds at most 20 documents.
Deleting a document immediately removes it from future retrieval; the worker
removes its chunks, vectors and private file. Historical approved replies and
answer excerpts remain as review history.

## 4. Chat in the Knowledge base dashboard

Choose a fresh workspace and open Knowledge base. Upload one file and wait for
Ready. Ask a question in **Company knowledge chat**. Answers retain their
supporting passages, which you can expand. **New chat** starts a separate
conversation; the conversation selector restores previous company threads.

For a follow-up such as "And what is its warranty?", recent questions help
resolve the subject. The model receives bounded conversation context, but prior
answers are not accepted as evidence. It must ground its new answer in the
currently retrieved company excerpts. A thread ID from another workspace is
rejected, including when the same account belongs to both workspaces.

Gemini-mode retrieval embeds the question, searches this workspace's Ready
chunks by pgvector cosine distance, considers eight candidates, drops those
below `RAG_MIN_SIMILARITY` (default 0.35), and keeps at most five sources.
If no supported evidence exists, the assistant says information is missing.
Generated answers must use valid returned citation IDs.

Local mode performs keyword ranking and returns labelled excerpts. It needs no
provider key, does not store semantic vectors and does not provide a reasoning
model's conversational synthesis. Gemini mode uses the configured provider's
quota for both embeddings and answers; errors do not silently switch modes.

For an uploadable corpus, use the [Northstar demo pack](demo/README.md). Upload
one company PDF/DOCX/Markdown copy, and keep the expected-answer guide separate.

## 5. Ticket preparation and human review

Create a ticket or load the built-in synthetic demo data in Local mode. Choose
Prepare draft. The API records a queued run and responds with Processing;
Redis dispatches it to the worker. The dashboard polls and displays the result.
The API does not wait for classification/retrieval/drafting to finish.

The worker's bounded Python pipeline is:

```text
Classify -> retrieve company policies -> inspect a matching order -> draft
                                                     -> await human review
```

The supplied design deliberately uses ordinary Python orchestration, so the
previous LangGraph dependency and graph builder are removed. The typed
read-only order tool retains Pydantic validation. Its workspace and customer
scope come from server records; the model cannot supply a URL, SQL or tenant ID.
One run has a 30-second preparation budget, and job retries are bounded.

Nothing in preparation mutates an order or delivers an unreviewed order reply.
An owner/admin reviews financial/replacement proposals; agents may review low-risk
replies and handoffs. Approval saves a decision and queues a durable action.
The worker rechecks the approver's live permissions, current policy/order
eligibility and ticket revision, then commits the outcome, customer reply and
audit event atomically. A unique delivery identity prevents duplicate replies;
a second approved proposal cannot refund an already-refunded sample order.

Reviewed replies now reach the actual customer portal conversation. The built-in
financial order changes are simulations and do not call a payment processor or
carrier. An optional external order-status HTTP adapter is available with your
own provider's compatible URL and server-only credentials. Email/SMS delivery is
not used; customers read their replies in the portal. See the
[customer workflow guide](CUSTOMER_WORKFLOW.md) for setup, identity, SSE,
automatic ticket creation, embedding and the external API contract.

Rich policy conditions such as delivery dates, photos and partial restocking
calculations still require human inspection; mock eligibility flags do not
prove every condition in an uploaded commercial policy.

## 6. Isolation and permissions

Business data carries a workspace ID. Routes verify membership before reading
or changing records. PostgreSQL RLS supplies a second wall even if a query
omits its application-level filter. Transactions set `app.user_id` and
`app.workspace_id` locally, and context is restored after commit/rollback.

Compose runs the API as `resolveai_app` and jobs as `resolveai_worker`. Neither
owns business tables, is a superuser or bypasses RLS. Migrations use the original
owner. The worker is trusted only for the workspace carried by each job; its
dispatcher can enumerate workspace IDs to recover pending jobs. The runtime
roles are not granted access to archived personal-chat tables.

Authentication now uses the PDF's 15-minute access JWT and 30-day rotating
refresh family. Access credentials stay in browser memory; the refresh cookie is
httpOnly and refresh values are hashed in PostgreSQL. Reuse revokes the whole
family, logout revokes the current family, and current membership/role is checked
for every request. Owners, admins and agents have separate permissions.

The active workspace comes from the signed JWT. Switching validates membership
and returns a new token; a body/query/compatibility-path ID cannot silently
change tenant context. New signups go through onboarding. Dedicated `/login`
and `/signup` pages make authentication visible instead of hiding it in the
home screen. Owners/admins invite teammates, manage roles and remove members;
only the owner can delete a workspace with name confirmation. Login/refresh,
role changes, invitation acceptance and deletion are audited.

Read [Authentication and tenancy](AUTH_AND_TENANCY.md) for the request flow,
files and API examples. All APIs use a safe error message and request ID. No new
paid API or key is needed for authentication or workspace membership.

## 7. AI boundary and debugging

`backend/ai/gemini.py` centralizes model access, fallback handling, diagnostics,
strict structured-output validation and one repair attempt. Versioned prompts
live under `backend/ai/prompts`. `backend/ai/embeddings.py` owns embedding calls.
Business routes and preparation logic do not call a provider SDK directly.

Pydantic's `additionalProperties` keyword is removed only from schemas sent to
Gemini; returned data remains strict. Citation validation rejects invented
source IDs. Credential-like generated output is blocked. Retrieved documents,
customer messages and previous answers are reference data, never approval or
instructions to execute an action.

```powershell
docker compose --env-file backend/.env exec api python check_gemini.py --structured --embeddings
```

This uses synthetic data and provider quota. A ready document establishes
ingestion, not generation availability. Model, quota, key and schema errors are
reported separately with redacted provider diagnostics. Request IDs and stored
run/audit metadata help investigate failures. Dashboard token counts are
observed usage, not invented dollar costs.

## 8. Files and verification

| Location                                         | Responsibility                                                              |
| ------------------------------------------------ | --------------------------------------------------------------------------- |
| `frontend/app/page.tsx`                          | Redirect to the company dashboard                                           |
| `frontend/app/resolve/dashboard.tsx`             | Unified dashboard, documents and persistent company chat                    |
| `frontend/next.config.ts`                        | Same-origin API proxy                                                       |
| `backend/main.py`                                | App composition, health, request IDs and error contract                     |
| `backend/resolveai/router.py`                    | Authenticated product API and approval boundary                             |
| `backend/resolveai/documents.py`                 | Safe file parsing and chunking                                              |
| `backend/resolveai/knowledge.py`                 | Company-only pgvector/keyword retrieval                                     |
| `backend/resolveai/storage.py`                   | Opaque private upload storage                                               |
| `backend/resolveai/jobs.py`                      | Redis/arq dispatch and recovery                                             |
| `backend/resolveai/worker.py`                    | Idempotent document ingestion/reindexing                                    |
| `backend/resolveai/tickets.py`                   | Background ticket preparation                                               |
| `backend/database/tenant.py`                     | Transaction-local tenant context                                            |
| `backend/database/security.py`                   | RLS policies and membership predicate                                       |
| `backend/database/migrations/versions/0005_*.py` | Additive pgvector/thread/storage migration and historical text re-embedding |
| `backend/database/migrations/versions/0006_*.py` | Durable draft-job metadata                                                  |

Run backend tests against a pgvector-enabled development database, with a role
able to create disposable test schemas. Tests explicitly translate ORM tables
into random schemas while resolving the vector extension from public; they do
not use or drop the application's tables. RLS tests use a non-owner role and
deliberately omit the workspace filter. Provider tests use a local HTTP
transport, and vector tests exercise the actual database cosine operator.

```powershell
cd backend
python -m pytest -q
python -m alembic check
```

Frontend checks are `npm run lint` and `npm run build`. Native mobile, a public
customer widget/SSE, production GCS/Cloud Run,
OpenTelemetry/Sentry and complete per-call dollar-cost accounting remain
separate items in the implementation map. The current work does not claim
their deployment or the PDF's unmeasured p95/evaluation targets.
