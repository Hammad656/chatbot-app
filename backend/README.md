# Morrow backend

FastAPI service for Morrow. PostgreSQL stores user accounts, conversations, and
messages. Passwords are Argon2-hashed; authenticated sessions use HTTP-only
cookies with CSRF protection. Gemini requests run on the server, so the API key
is never exposed to the browser.

The `database/` directory contains SQLAlchemy models and Alembic migrations.
PostgreSQL itself stores database files in its own server-managed data directory.

## Requirements

- Python 3.11 or later (tested with Python 3.12)
- PostgreSQL with the `chatbot-db` database created

## Configure

From the project root, copy the example file only if you do not already have a
private `backend/.env`:

```powershell
Copy-Item backend\.env.example backend\.env
```

In `backend/.env`, set:

- `GEMINI_API_KEY` to your Google Gemini API key.
- Optionally set `GEMINI_MODEL` (defaults to `gemini-flash-latest`) and
  `GEMINI_TIMEOUT_SECONDS` (defaults to `45`).
  If Google rejects that model with 404, or returns a temporary 503, the backend
  lists models using the same API key and retries generation at most once with
  a different advertised stable Flash text model supporting `generateContent`.
  It prefers the Flash alias for 404 and the Flash Lite alias for 503; it never
  silently switches to Pro, preview, image, audio, or embedding models.
  An existing `GEMINI_MODEL` in your private `.env` overrides the code default.
- `PINECONE_API_KEY` to a Pinecone API key.
- `PINECONE_INDEX_NAME` to the name of your Pinecone index (for example,
  `blooming-peach`). The index must have an integrated embedding field named
  `text`; the current Pinecone setup uses that field to embed CV passages and
  search questions. The backend does not generate separate embeddings.
  Replace the example API key with your real key; the literal `your-key` is only
  a placeholder and cannot authenticate.
- `POSTGRES_PASSWORD` to the password for the PostgreSQL user in `POSTGRES_USER`.
- `JWT_SECRET_KEY` to a long, random value. Generate one with:

  ```powershell
  py -c "import secrets; print(secrets.token_urlsafe(48))"
  ```

The local database defaults are `localhost:5432`, user `postgres`, and database
`chatbot-db`. Change `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, or
`POSTGRES_DB` if your PostgreSQL setup is different. Keep `.env` private and
never commit or share it. For production, set `COOKIE_SECURE=true` and update
`CORS_ORIGINS` to the exact HTTPS frontend origin.

## Install and migrate

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
```

The migrations create users, conversations, messages, revocable login sessions,
and CV indexing metadata. Deleting a user deletes their database records. Login
sessions expire after seven days. Run `alembic upgrade head` again whenever the
database schema changes.

## Diagnose Gemini configuration

A `404 NOT_FOUND` mentioning `models/gemini-1.5-flash` means that the model
selected by the running process is unavailable or does not support
`generateContent`. Changing only the API key does not change `GEMINI_MODEL`.
Replace that stale setting with a model available to your Google AI Studio
project. The default alias in `.env.example` follows the official Python SDK's
Flash examples; availability still depends on the project and current service.

From the backend directory, using the same virtual environment as Uvicorn:

```powershell
.\.venv\Scripts\python.exe check_gemini.py
```

This lists generation model names without printing the API key. Copy a suitable
Flash text model name into `GEMINI_MODEL` in your existing private `.env`.
To also make a small generation request (uses Gemini quota):

```powershell
.\.venv\Scripts\python.exe check_gemini.py --test
```

Stop and restart Uvicorn after changing `.env`; editing `.env` does not reliably
restart a running backend. Existing exported environment variables take
precedence over `.env`. If the model shown by the diagnostic or `/health` still
looks wrong, check for an exported `GEMINI_MODEL` override or a backend process
running from another checkout. The health check shows the configured model,
but does not prove provider connectivity or quota availability.

The chat API reports model/configuration failures as 503, Google quota/rate
limits as 429, and other provider failures as 502. It does not retry quota or
API-key errors across other models. Diagnostic output and public error messages
do not include credential values or raw provider response bodies.

A hydration warning involving `cz-shortcut-listen` on `<body>` is separate from
a provider error. Browser extensions can inject that attribute before React
hydrates. Test in a browser profile with extensions disabled; do not hide all
hydration warnings to work around it.

## CV retrieval-augmented chat

After signing in, use **Upload CV** in the sidebar to upload a PDF or DOCX file
(maximum 8 MB). The backend extracts selectable text, splits it into overlapping
passages, and upserts those passages into the configured Pinecone index. Each
account gets its own Pinecone namespace, and every search is additionally
filtered to the account's current CV. To correct a mistaken upload, select
**Replace CV** and upload the right PDF or DOCX; the new CV becomes active after
it is indexed, and the previous CV is then removed. **Remove** deletes all
indexed passages in that user's namespace.

Chat is CV-only: without an uploaded CV, the assistant asks the user to upload
one. With a CV, every question searches only the current document in that user's
namespace. The backend retrieves up to ten passages without a fixed similarity
cutoff, since useful matches can score differently for short CVs and follow-up
questions. Recent conversation turns help resolve follow-ups and improve the
search query; the model is explicitly told they are not evidence. Only retrieved
CV excerpts support facts about the user, and the model cites their section and
excerpt labels. General questions unrelated to the CV are out of scope. Scanned
PDFs require OCR before upload; OCR is not included.

The original file is not retained. Extracted passages are stored by Pinecone,
and relevant passages are sent to Gemini during CV-aware chat. Keep the Gemini
and Pinecone API keys private in `backend/.env`; never put them in the frontend.

## Run

```powershell
uvicorn main:app --reload
```

The API runs at `http://localhost:8000`; interactive API documentation is
available at `http://localhost:8000/docs`. `GET /health` reports whether AI,
database, and CV RAG settings are present. Account endpoints are under `/api/auth`, chats
are under `/api/chats`, and `POST /api/chat/{conversation_id}` saves a message
and its AI response to that user's conversation.

## ResolveAI support workspace

The support web MVP is available at `/resolve`. Read the [implementation and learning guide](../docs/RESOLVEAI.md) for setup, document workers, tenant roles, LangGraph/tools, approval guarantees, testing and Docker. Run `alembic upgrade head` after installing the updated requirements. Existing CV endpoints remain available.
