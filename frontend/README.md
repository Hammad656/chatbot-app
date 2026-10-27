# Morrow frontend

Responsive Next.js chat interface for the Morrow AI assistant, with email and
password signup/login. Authenticated conversations are loaded from PostgreSQL
through the FastAPI backend and are available across browsers after login.

## Run locally

1. Configure PostgreSQL, run the Alembic migration, and start the
   [FastAPI backend](../backend/README.md).
2. From this directory, configure the backend URL and start Next.js:

   ```powershell
   Copy-Item .env.example .env.local
   npm install
   npm run dev
   ```

3. Open [http://localhost:3000](http://localhost:3000), create an account, and
   start a conversation.

The browser calls the backend at `NEXT_PUBLIC_API_URL` (default:
`http://localhost:8000`). The OpenAI key, PostgreSQL password, and session
signing secret belong in `backend/.env` only. Authentication uses secure,
HTTP-only session cookies; chat history is not stored in browser local storage.

## Other commands

```powershell
npm run lint
npm run build
```
