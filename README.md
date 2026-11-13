# ResolveAI

One company-knowledge and support application at **http://localhost:3000**.
Upload company documents in **Knowledge base**, then chat with cited answers in
the same dashboard. The personal CV chatbot and its APIs have been retired.
Existing accounts, company documents and support records are preserved.

The local stack is Next.js, FastAPI, a separate Redis/arq worker, PostgreSQL 17
with pgvector, and private document storage. The browser uses relative
`/api/v1` requests; Next.js proxies them to the internal API. Only the web
service publishes a port. The old `/resolve` address redirects to `/`.

## Start or update

Keep your existing private `backend/.env`, including your PostgreSQL password,
JWT signing key, and Gemini API key. From the project root on `dev`:

```powershell
git pull --ff-only origin dev
docker compose --env-file backend/.env up --build -d
docker compose --env-file backend/.env ps
```

The API applies additive migrations, provisions non-owner database roles and
starts serving. The worker handles document ingestion and ticket preparation.
The PostgreSQL image stays on major version 17, so the existing named database
volume can be reused. Original uploads now use a private shared volume.

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

The broader design includes a public customer widget, rotating refresh tokens,
streaming, detailed model-cost accounting and production cloud integrations.
The implementation map distinguishes those items from the company-dashboard
workflow delivered here; this repository does not claim a complete production
rollout or the design's unmeasured latency/evaluation targets.
