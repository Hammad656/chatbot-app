# ResolveAI frontend

The Next.js app serves one company application. `/` and `/resolve` redirect to
`/knowledge`; logged-out visitors are sent to `/login`.
There is no personal CV application. Sign in, choose a workspace, upload a
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

## Auth and navigation

Dedicated pages: `/login`, `/signup`, `/onboarding`, `/dashboard`, `/knowledge`,
`/tickets`, `/conversations`, `/approvals`, `/mock-orders`, `/activity`, `/settings`, `/settings/members`, `/invite`.
All use the same origin and API; these are pages in one app, not separate servers.

`auth-form.tsx` provides visible sign-in/signup links, actionable errors, password
visibility, and a POST fallback that keeps credentials out of URLs.
`resolve/api.ts` stores the access token only in memory, serializes cookie-changing
auth operations across tabs, and retries an expired-token request once. A change
of account/workspace during refresh clears the old view instead of replaying a
mutation against another company. Logout notifies the other tabs.

`resolve/workspace-panels.tsx` implements invitations, role changes, membership
removal, settings and owner-only workspace deletion. Invitation secrets use a
URL fragment and a short-lived pending-invitation sessionStorage entry; access
and refresh credentials never use localStorage/sessionStorage.

## Browser regression checks

With a running development stack at `http://127.0.0.1:3000`:

```powershell
npm ci
npx playwright install chromium
npm run test:e2e
```

Set `E2E_BASE_URL` to use another development origin. Tests create synthetic
accounts/workspaces and use the actual document worker in Local demo mode.
They do not call a model provider. CI creates its own disposable Compose stack.

## Customer portal and visual update

The navy glass palette uses cyan/violet accents, semantic status colors and
motion that respects reduced-motion preferences. A prominent header logout
button and live staff role label appear throughout the dashboard.

`/widget/[workspaceKey]` is the customer support view in the same app. It shows
Guest customer or Customer · Signed in, keeps credentials in memory, streams
validated answers, polls for reviewed replies, restores one-hour sessions and
clears cross-tab/cached identity data on logout. `public/widget.js` embeds that
view in an iframe. `proxy.ts` supplies the exact-origin framing CSP.

Workspace Settings enables chat, manages embed origins and issues private customer
access links. Conversations, Approvals and ticket controls expose intake, handoffs,
assignment, review and delivery outcomes. See
[Customer workflow](../docs/CUSTOMER_WORKFLOW.md) for the complete test sequence.
