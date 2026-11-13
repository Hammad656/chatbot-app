# ResolveAI frontend

The Next.js app serves the company dashboard at `/`. `/resolve` redirects home;
there is no personal CV application. Sign in, choose a workspace, upload a
company document in **Knowledge base**, and start or resume a cited conversation.

The typed API helper sends relative `/api/v1/...` requests. Next.js rewrites
them to `API_INTERNAL_URL` on the server, default `http://127.0.0.1:8000` for
standalone development. Compose builds it with `http://api:8000`. Browser code
never needs a separate API origin or provider credentials. The previous
`NEXT_PUBLIC_API_URL` setting is no longer used.

Use the [root Docker instructions](../README.md) to run the whole project.
For frontend-only development with a separately running backend:

```powershell
npm ci
npm run dev
```

Check changes using `npm run lint` and `npm run build`. The Knowledge base view
polls queued documents. Ticket drafts return immediately with a processing
state, and the view refreshes when the separate worker completes preparation.
Company chat history is persisted per workspace and conversation. Switching
workspaces clears the active conversation; stale network responses are ignored.

The UI retains the dark glass theme, responsive layout, motion preferences,
sources, mock orders, reviewer controls, audit history and team membership.
