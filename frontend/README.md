# Morrow frontend

Responsive Next.js chat interface for the Morrow AI assistant.

## Run locally

1. Start the [FastAPI backend](../backend/README.md) and add your OpenAI API key
   to `backend/.env`.
2. From this directory, configure the backend URL and start Next.js:

   ```powershell
   Copy-Item .env.example .env.local
   npm install
   npm run dev
   ```

3. Open [http://localhost:3000](http://localhost:3000).

The browser calls the backend at `NEXT_PUBLIC_API_URL` (default:
`http://localhost:8000`). The OpenAI key belongs in the backend `.env` only; do
not add it to a `NEXT_PUBLIC_` variable. Conversations are saved in this
browser's local storage.

## Other commands

```powershell
npm run lint
npm run build
```
