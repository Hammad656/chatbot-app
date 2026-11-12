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

   After signing in, use **Upload CV** in the sidebar to index a PDF or DOCX for
   CV-aware chat. Pinecone stores the extracted passages; only relevant passages
   are sent to Gemini with a question. Use **Remove** to delete the indexed CV data.

The browser calls the backend at `NEXT_PUBLIC_API_URL` (default:
`http://localhost:8000`). The Gemini and Pinecone keys, PostgreSQL password,
and session signing secret belong in `backend/.env` only. Authentication uses
secure, HTTP-only session cookies; chat history is not stored in browser local
storage.

## Other commands

```powershell
npm run lint
npm run build
```

## ResolveAI

Open `/resolve` for the support dashboard. It uses the existing login and backend URL. Create a Local demo workspace and load demo data to try the workflow without provider keys. See [the learning guide](../docs/RESOLVEAI.md) for the architecture and startup steps.
