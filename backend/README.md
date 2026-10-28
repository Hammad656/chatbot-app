# Morrow backend

FastAPI service for Morrow. PostgreSQL stores user accounts, conversations, and
messages. Passwords are Argon2-hashed; authenticated sessions use HTTP-only
cookies with CSRF protection. OpenAI requests run on the server, so the API key
is never exposed to the browser.

The `database/` directory contains SQLAlchemy models and Alembic migrations.
PostgreSQL itself stores database files in its own server-managed data directory.

## Requirements

- Python 3.10 or later
- PostgreSQL with the `chatbot-db` database created

## Configure

From the project root, copy the example file only if you do not already have a
private `backend/.env`:

```powershell
Copy-Item backend\.env.example backend\.env
```

In `backend/.env`, set:

- `OPENAI_API_KEY` to your OpenAI key.
- `PINECONE_API_KEY` to a Pinecone API key.
- `PINECONE_INDEX_NAME` to the name of your Pinecone index (for example,
  `blooming-peach`). The index must have an integrated embedding field named
  `text`; the current Pinecone setup uses that field to embed CV passages and
  search questions. The backend does not generate separate OpenAI embeddings.
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

## CV retrieval-augmented chat

After signing in, use **Upload CV** in the sidebar to upload a PDF or DOCX file
(maximum 8 MB). The backend extracts selectable text, splits it into overlapping
passages, and upserts those passages into the configured Pinecone index. Each
account gets its own Pinecone namespace, and every search is additionally
filtered to the account's current CV. Replacing a CV updates that reference;
**Remove** deletes all indexed passages in that user's namespace.

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
and relevant passages are sent to OpenAI during CV-aware chat. Keep both API
keys private in `backend/.env`; never put them in the frontend.

## Run

```powershell
uvicorn main:app --reload
```

The API runs at `http://localhost:8000`; interactive API documentation is
available at `http://localhost:8000/docs`. `GET /health` reports whether AI,
database, and CV RAG settings are present. Account endpoints are under `/api/auth`, chats
are under `/api/chats`, and `POST /api/chat/{conversation_id}` saves a message
and its AI response to that user's conversation.
