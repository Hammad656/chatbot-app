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

Run Alembic migrations whenever the database schema changes. The initial
migration creates `users`, `conversations`, and `messages`; deleting a user
deletes their sessions, conversations, and messages. Login sessions are
server-revocable and expire after seven days.

## Run

```powershell
uvicorn main:app --reload
```

The API runs at `http://localhost:8000`; interactive API documentation is
available at `http://localhost:8000/docs`. `GET /health` reports whether AI and
database settings are present. Account endpoints are under `/api/auth`, chats
are under `/api/chats`, and `POST /api/chat/{conversation_id}` saves a message
and its AI response to that user's conversation.
