"use client";

import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import LiquidOrb from "../liquid-orb";
import Link from "next/link";
import { useRouter, useParams } from "next/navigation";
import { TeamPanel, SettingsPanel } from "./workspace-panels";
import {
  APIError,
  request,
  acceptSession,
  restoreSession,
  signOut as endSession,
  clearSession,
  subscribeSession,
  currentUser,
} from "./api";
import type {
  Answer,
  AuthSession,
  Snapshot,
  Source,
  TicketDetail,
  User,
  Workspace,
} from "./types";
import "./support.css";

type View =
  | "overview"
  | "tickets"
  | "knowledge"
  | "orders"
  | "activity"
  | "team"
  | "settings";
const pagePaths: Record<View, string> = {
  overview: "/dashboard",
  tickets: "/tickets",
  knowledge: "/knowledge",
  orders: "/mock-orders",
  activity: "/activity",
  team: "/settings/members",
  settings: "/settings",
};
const pages: { id: View; label: string; icon: string; note: string }[] = [
  {
    id: "overview",
    label: "Overview",
    icon: "grid",
    note: "A clear view of your support workspace.",
  },
  {
    id: "tickets",
    label: "Support inbox",
    icon: "inbox",
    note: "From customer question to a reviewed resolution.",
  },
  {
    id: "knowledge",
    label: "Knowledge base",
    icon: "book",
    note: "Ground every answer in your company documents.",
  },
  {
    id: "orders",
    label: "Mock orders",
    icon: "box",
    note: "A safe place to practice tool-driven operations.",
  },
  {
    id: "activity",
    label: "Activity & traces",
    icon: "pulse",
    note: "Understand what happened, who approved it, and why.",
  },
  {
    id: "team",
    label: "Workspace team",
    icon: "users",
    note: "Give teammates the right level of access.",
  },
  {
    id: "settings",
    label: "Settings",
    icon: "shield",
    note: "Your company workspace, your preferences.",
  },
];
function Icon({ name, size = 19 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="2" />
        <rect x="14" y="3" width="7" height="7" rx="2" />
        <rect x="3" y="14" width="7" height="7" rx="2" />
        <rect x="14" y="14" width="7" height="7" rx="2" />
      </>
    ),
    inbox: (
      <>
        <path d="m4 4-2 11v5h20v-5L20 4H4Z" />
        <path d="M2 15h6l2 3h4l2-3h6" />
      </>
    ),
    book: (
      <>
        <path d="M12 5v16m0-16C8 2 4 3 2 4v15c4-1 7 0 10 2 3-2 6-3 10-2V4c-3-1-6-2-10 1Z" />
      </>
    ),
    box: (
      <>
        <path d="m12 2 10 5v10l-10 5L2 17V7l10-5Zm0 10v10M2 7l10 5 10-5M7 4.5l10 5" />
      </>
    ),
    pulse: (
      <>
        <path d="M2 12h5l3-9 4 18 3-9h5" />
      </>
    ),
    users: (
      <>
        <circle cx="9" cy="8" r="4" />
        <path d="M2 21v-3a5 5 0 0 1 5-5h4a5 5 0 0 1 5 5v3m0-17a4 4 0 0 1 0 8m4 9v-3a5 5 0 0 0-3-4" />
      </>
    ),
    spark: (
      <>
        <path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Z" />
      </>
    ),
    plus: <path d="M12 4v16M4 12h16" />,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    refresh: (
      <>
        <path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5" />
      </>
    ),
    shield: (
      <>
        <path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    upload: (
      <>
        <path d="M12 16V3m-5 5 5-5 5 5M3 16v5h18v-5" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.spark}
    </svg>
  );
}
function Badge({ value }: { value: string }) {
  return (
    <span className={`r-badge r-${value.replaceAll("_", "-")}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
function Sources({ sources }: { sources: Source[] }) {
  return (
    <div className="r-sources">
      {sources.map((source) => (
        <details key={`${source.document_id}-${source.position}`}>
          <summary>
            <Icon name="book" size={14} /> [{source.id}] {source.filename}{" "}
            <span>{source.section}</span>
          </summary>
          <blockquote>{source.text}</blockquote>
        </details>
      ))}
    </div>
  );
}
function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="r-empty">
      <span>
        <Icon name="spark" size={25} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
function date(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function money(cents: number, currency: string) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(
    cents / 100,
  );
}

export default function ResolveAI({
  initialView = "knowledge",
}: {
  initialView?: View;
}) {
  const router = useRouter();
  const instanceId = useId();
  const liveProfile = useSyncExternalStore(
    subscribeSession,
    currentUser,
    () => null,
  );
  const params = useParams<{ ticketId?: string }>();
  const ticketParam = params.ticketId;
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const token = "";
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const view = initialView;
  const [ticketState, setTicket] = useState<TicketDetail | null>(null);
  const ticket =
    ticketState && (!ticketParam || ticketState.id === ticketParam)
      ? ticketState
      : null;
  const [reply, setReply] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const workspaceReady = Boolean(snapshot);
  const authTransition =
    user === undefined ||
    (liveProfile
      ? user?.id !== liveProfile.id ||
        (!!workspaceId && workspaceId !== liveProfile.active_workspace_id)
      : !!user);
  const [ticketFilter, setTicketFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState<"workspace" | "ticket" | "order" | null>(
    null,
  );
  const [question, setQuestion] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<{
    workspace: string;
    thread: string;
    answers: Answer[];
  }>({ workspace: "", thread: "new", answers: [] });
  const selectedThread = threadId ?? snapshot?.threads[0]?.id ?? "new";
  const answers =
    conversation.workspace === workspaceId &&
    conversation.thread === selectedThread
      ? conversation.answers
      : [];
  const activeWorkspace = useRef("");
  const activeTicket = useRef("");
  const reviewKeys = useRef(new Map<string, string>());
  const dialog = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const data = await request<Snapshot>(`/api/v1/workspaces/${id}/overview`);
      if (activeWorkspace.current === id) setSnapshot(data);
    } catch (error) {
      if (activeWorkspace.current === id)
        setNotice(
          error instanceof Error ? error.message : "Could not load workspace.",
        );
    } finally {
      if (activeWorkspace.current === id) setLoading(false);
    }
  }, []);
  const chooseWorkspace = useCallback(
    async (id: string, alreadyActive = false) => {
      activeWorkspace.current = "";
      activeTicket.current = "";
      reviewKeys.current.clear();
      setSnapshot(null);
      setModal(null);
      setTicket(null);
      setReply("");
      setNotice("");
      setSearch("");
      setQuestion("");
      setThreadId(null);
      setConversation({ workspace: id, thread: "new", answers: [] });
      setWorkspaceId(id);
      if (!id) return;
      setBusy("switch");
      try {
        if (!alreadyActive) {
          const session = await request<AuthSession>(
            `/api/v1/workspaces/${id}/switch`,
            { method: "POST" },
          );
          acceptSession(session);
          setUser(session.user);
        }
        activeWorkspace.current = id;
        await refresh(id);
      } catch (error) {
        setNotice(
          error instanceof Error
            ? error.message
            : "Could not switch workspace.",
        );
        setWorkspaceId("");
      } finally {
        setBusy("");
      }
    },
    [refresh],
  );
  useEffect(() => {
    let mounted = true;
    async function restore() {
      try {
        const account = await restoreSession();
        const list = await request<Workspace[]>("/api/v1/workspaces");
        if (!mounted) return;
        setUser(account);
        setWorkspaces(list);
        const selected =
          list.find(
            (workspace) => workspace.id === account.active_workspace_id,
          ) ?? list[0];
        if (selected)
          await chooseWorkspace(
            selected.id,
            selected.id === account.active_workspace_id,
          );
      } catch (error) {
        if (!mounted) return;
        if (error instanceof APIError && error.status === 401) {
          setUser(null);
          router.replace("/login");
        } else {
          setUser(null);
          setNotice(
            error instanceof Error
              ? error.message
              : "Could not restore your session.",
          );
        }
      }
    }
    void restore();
    const lost = () => {
      clearSession();
      setUser(null);
      activeWorkspace.current = "";
      setSnapshot(null);
      router.replace("/login");
    };
    window.addEventListener("resolveai-session-ended", lost);
    const changed = () => {
      activeWorkspace.current = "";
      setSnapshot(null);
      setUser(undefined);
      window.location.replace("/knowledge");
    };
    window.addEventListener("resolveai-workspace-changed", changed);
    const channel =
      typeof BroadcastChannel !== "undefined"
        ? new BroadcastChannel("resolveai-auth")
        : null;
    if (channel)
      channel.onmessage = (event) => {
        if (event.data === "logout") lost();
      };
    return () => {
      mounted = false;
      window.removeEventListener("resolveai-session-ended", lost);
      window.removeEventListener("resolveai-workspace-changed", changed);
      channel?.close();
    };
  }, [chooseWorkspace, router]);
  useEffect(() => {
    let active = true;
    if (workspaceId && selectedThread !== "new") {
      void request<Answer[]>(
        `/api/v1/workspaces/${workspaceId}/threads/${selectedThread}`,
      )
        .then((data) => {
          if (active)
            setConversation({
              workspace: workspaceId,
              thread: selectedThread,
              answers: data,
            });
        })
        .catch((error) => {
          if (active)
            setNotice(
              error instanceof Error
                ? error.message
                : "Could not load conversation.",
            );
        });
    }
    return () => {
      active = false;
    };
  }, [workspaceId, selectedThread]);
  const pendingDocs =
    snapshot?.documents.some((doc) =>
      ["queued", "processing", "deleting"].includes(doc.status),
    ) ?? false;
  const pendingWork =
    pendingDocs ||
    (snapshot?.tickets.some((item) => item.status === "processing") ?? false);
  useEffect(() => {
    if (!pendingWork || !workspaceId) return;
    const timer = window.setInterval(() => void refresh(workspaceId), 2000);
    return () => window.clearInterval(timer);
  }, [pendingWork, workspaceId, refresh]);
  useEffect(() => {
    const updated = snapshot?.tickets.find((item) => item.id === ticket?.id);
    if (
      ticket?.status !== "processing" ||
      !updated ||
      updated.status === "processing"
    )
      return;
    let active = true;
    void request<TicketDetail>(
      `/api/v1/workspaces/${workspaceId}/tickets/${ticket.id}`,
    )
      .then((data) => {
        if (active) {
          setTicket(data);
          setReply(data.draft ?? "");
        }
      })
      .catch((error) => {
        if (active)
          setNotice(
            error instanceof Error ? error.message : "Could not refresh draft.",
          );
      });
    return () => {
      active = false;
    };
  }, [snapshot, workspaceId, ticket]);
  useEffect(() => {
    const element = dialog.current;
    if (modal && !authTransition) element?.showModal();
    return () => {
      if (element?.open) element.close();
    };
  }, [modal, authTransition]);
  useEffect(() => {
    if (!ticketParam || !workspaceId || !workspaceReady) return;
    let active = true;
    activeTicket.current = ticketParam;
    void request<TicketDetail>(
      `/api/v1/workspaces/${workspaceId}/tickets/${ticketParam}`,
    )
      .then((data) => {
        if (active) {
          setTicket(data);
          setReply(data.draft ?? "");
        }
      })
      .catch((error) => {
        if (active)
          setNotice(
            error instanceof Error ? error.message : "Could not load ticket.",
          );
      });
    return () => {
      active = false;
    };
  }, [ticketParam, workspaceId, workspaceReady]);

  async function perform(label: string, operation: () => Promise<void>) {
    setBusy(label);
    setNotice("");
    try {
      await operation();
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "The request failed. Please retry.",
      );
    } finally {
      setBusy("");
    }
  }
  async function signOut() {
    await perform("logout", async () => {
      await endSession();
      setUser(null);
      setWorkspaces([]);
      await chooseWorkspace("");
      router.replace("/login");
    });
  }
  const base = `/api/v1/workspaces/${workspaceId}`;
  const admin =
    snapshot?.workspace.role === "admin" ||
    snapshot?.workspace.role === "owner";
  const canReview =
    admin || ["reply", "escalate"].includes(ticket?.proposed_action.kind ?? "");
  async function openTicket(id: string) {
    router.push(`/tickets/${id}`);
  }
  async function draftTicket() {
    if (!ticket) return;
    await perform("draft", async () => {
      const data = await request<TicketDetail>(
        `${base}/tickets/${ticket.id}/draft`,
        { method: "POST" },
        token,
      );
      setTicket(data);
      setReply(data.draft ?? "");
      await refresh(workspaceId);
    });
    // Errors are also recorded on the ticket by the server.
    await refresh(workspaceId);
  }
  async function review(decision: "approve" | "reject") {
    if (!ticket) return;
    const identity = JSON.stringify([
      workspaceId,
      ticket.id,
      ticket.revision,
      decision,
      reply,
    ]);
    if (!reviewKeys.current.has(identity))
      reviewKeys.current.set(identity, crypto.randomUUID());
    await perform("review", async () => {
      const data = await request<{ ticket: TicketDetail }>(
        `${base}/tickets/${ticket.id}/review`,
        {
          method: "POST",
          body: JSON.stringify({
            request_id: reviewKeys.current.get(identity),
            expected_revision: ticket.revision,
            decision,
            reply,
          }),
        },
        token,
      );
      setTicket(data.ticket);
      setReply(data.ticket.draft ?? "");
      await refresh(workspaceId);
    });
  }
  async function createRecord(
    event: FormEvent<HTMLFormElement>,
    kind: "workspace" | "ticket" | "order",
  ) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await perform("create", async () => {
      if (kind === "workspace") {
        const workspace = await request<Workspace>(
          "/api/v1/workspaces",
          {
            method: "POST",
            body: JSON.stringify({
              name: form.get("name"),
              ai_mode: form.get("ai_mode"),
            }),
          },
          token,
        );
        setWorkspaces((current) => [...current, workspace]);
        setModal(null);
        await chooseWorkspace(workspace.id);
        router.replace("/knowledge");
      } else if (kind === "ticket") {
        const data = await request<TicketDetail>(
          `${base}/tickets`,
          {
            method: "POST",
            body: JSON.stringify({
              subject: form.get("subject"),
              customer_email: form.get("customer_email"),
              description: form.get("description"),
              order_reference:
                String(form.get("order_reference") ?? "")
                  .trim()
                  .toUpperCase() || null,
            }),
          },
          token,
        );
        setModal(null);
        activeTicket.current = data.id;
        setTicket(data);
        setReply("");
        router.push(`/tickets/${data.id}`);
        await refresh(workspaceId);
      } else {
        await request(
          `${base}/orders`,
          {
            method: "POST",
            body: JSON.stringify({
              reference: String(form.get("reference")).trim().toUpperCase(),
              customer_email: form.get("customer_email"),
              item: form.get("item"),
              amount_cents: Math.round(Number(form.get("amount")) * 100),
              currency: "USD",
              status: form.get("status"),
              refundable: form.get("refundable") === "on",
              replaceable: form.get("replaceable") === "on",
            }),
          },
          token,
        );
        setModal(null);
        await refresh(workspaceId);
      }
    });
  }
  async function upload(file: File) {
    if (file.size > 20 * 1024 * 1024) {
      setNotice("Choose a document up to 20 MB.");
      return;
    }
    await perform("upload", async () => {
      const form = new FormData();
      form.append("file", file);
      await request(`${base}/documents`, { method: "POST", body: form }, token);
      await refresh(workspaceId);
    });
  }
  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const askingWorkspace = workspaceId;
    await perform("ask", async () => {
      const answer = await request<Answer>(
        `${base}/ask`,
        {
          method: "POST",
          body: JSON.stringify({
            question,
            thread_id: selectedThread !== "new" ? selectedThread : null,
          }),
        },
        token,
      );
      if (activeWorkspace.current !== askingWorkspace) return;
      setConversation({
        workspace: askingWorkspace,
        thread: answer.thread_id ?? "new",
        answers: [...answers, answer],
      });
      setThreadId(answer.thread_id);
      setQuestion("");
      await refresh(workspaceId);
    });
  }
  const currentPage = pages.find((page) => page.id === view)!;
  const filteredTickets =
    snapshot?.tickets.filter(
      (t) =>
        (ticketFilter === "all" || t.status === ticketFilter) &&
        `${t.subject} ${t.customer_email}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    ) ?? [];

  return (
    <main className="resolve-app">
      {notice && !modal && !authTransition && (
        <div className="r-notice" role="alert">
          <span>{notice}</span>
          <button aria-label="Dismiss notice" onClick={() => setNotice("")}>
            <Icon name="close" size={16} />
          </button>
        </div>
      )}
      {user === undefined || authTransition ? (
        <div className="r-auth-shell">
          <LiquidOrb />
          <p>Preparing your support workspace…</p>
        </div>
      ) : user === null ? (
        <div className="r-auth-shell">
          <section className="r-auth-card">
            <h1>Sign in to ResolveAI</h1>
            <p>Continue to your company workspace.</p>
            <Link className="r-button r-primary" href="/login">
              Sign in
            </Link>
            <Link className="r-button" href="/signup">
              Create account
            </Link>
            <button
              className="r-text-button"
              onClick={() => window.location.reload()}
            >
              Retry connection
            </button>
          </section>
        </div>
      ) : (
        <div className="r-shell">
          <aside className="r-sidebar">
            <Link className="r-logo" href="/">
              <span>
                <Icon name="spark" />
              </span>
              <strong>
                resolve<span>AI</span>
              </strong>
            </Link>
            <div className="r-workspace-switch">
              <label htmlFor={`${instanceId}-workspace`}>WORKSPACE</label>
              <select
                id={`${instanceId}-workspace`}
                value={workspaceId}
                disabled={!!busy || !workspaces.length}
                onChange={(event) => void chooseWorkspace(event.target.value)}
              >
                {!workspaces.length && (
                  <option value="">Create your first workspace</option>
                )}
                {workspaces.map((workspace) => (
                  <option value={workspace.id} key={workspace.id}>
                    {workspace.name}
                  </option>
                ))}
              </select>
              <button
                className="r-text-button"
                onClick={() => setModal("workspace")}
                disabled={!!busy}
              >
                <Icon name="plus" size={13} /> New workspace
              </button>
            </div>
            <nav aria-label="Support navigation">
              {pages
                .filter(
                  (page) => admin || !["orders", "activity"].includes(page.id),
                )
                .map((page) => (
                  <Link
                    key={page.id}
                    href={pagePaths[page.id]}
                    className={view === page.id ? "r-nav-active" : ""}
                    aria-current={view === page.id ? "page" : undefined}
                  >
                    <Icon name={page.icon} />
                    <span>{page.label}</span>
                    {page.id === "tickets" &&
                      !!snapshot?.metrics.statuses.awaiting_approval && (
                        <small>
                          {snapshot.metrics.statuses.awaiting_approval}
                        </small>
                      )}
                  </Link>
                ))}
            </nav>
            <div className="r-sidebar-foot">
              <div className="r-control-note">
                <Icon name="shield" />
                <strong>Humans make the call.</strong>
                <p>
                  Every draft waits for review. Order actions and delivery are
                  simulated.
                </p>
              </div>
              <div className="r-account">
                <span>{user.email[0].toUpperCase()}</span>
                <div>
                  <strong>{user.full_name || "Your account"}</strong>
                  <small title={user.email}>{user.email}</small>
                </div>
                <button
                  aria-label="Sign out"
                  title="Sign out"
                  onClick={() => void signOut()}
                  disabled={!!busy}
                >
                  Sign out
                </button>
              </div>
            </div>
          </aside>
          <section className="r-main">
            <header className="r-topbar">
              <span>
                <span className="r-online-dot" /> Support workspace
              </span>
              <div>
                {snapshot && (
                  <>
                    <Badge
                      value={
                        snapshot.workspace.ai_mode === "local"
                          ? "local_demo"
                          : "gemini"
                      }
                    />
                    <Badge value={snapshot.workspace.role} />
                  </>
                )}
                <button
                  className="r-icon-button"
                  aria-label="Refresh workspace"
                  disabled={!workspaceId || loading}
                  onClick={() => void refresh(workspaceId)}
                >
                  <Icon name="refresh" size={17} />
                </button>
              </div>
            </header>
            <div className="r-page" aria-busy={!!busy || loading}>
              <div className="r-page-heading">
                <div>
                  <span className="r-eyebrow">
                    {snapshot?.workspace.name ?? "LET’S GET YOU STARTED"}
                  </span>
                  <h1>{currentPage.label}</h1>
                  <p>{currentPage.note}</p>
                </div>
                {snapshot && (
                  <div className="r-heading-actions">
                    {admin && snapshot.workspace.ai_mode === "local" && (
                      <button
                        className="r-button"
                        disabled={!!busy}
                        onClick={() =>
                          void perform("seed", async () => {
                            await request(
                              `${base}/demo`,
                              { method: "POST" },
                              token,
                            );
                            await refresh(workspaceId);
                          })
                        }
                      >
                        {busy === "seed" ? "Loading…" : "Load demo data"}
                      </button>
                    )}
                    <button
                      className="r-button r-primary"
                      disabled={!!busy}
                      onClick={() => setModal("ticket")}
                    >
                      <Icon name="plus" size={15} /> New ticket
                    </button>
                  </div>
                )}
              </div>
              {!workspaceId ? (
                <div className="r-welcome-workspace">
                  <LiquidOrb />
                  <h2>Make space for better support.</h2>
                  <p>
                    Create a workspace to bring your policies, tickets, and team
                    together.
                    <br />
                    Local demo mode works without provider keys.
                  </p>
                  <button
                    className="r-button r-primary"
                    onClick={() => setModal("workspace")}
                  >
                    Create workspace <Icon name="arrow" size={16} />
                  </button>
                </div>
              ) : !snapshot ? (
                <div className="r-skeleton" role="status">
                  Loading workspace…
                </div>
              ) : (
                <>
                  {snapshot.workspace.ai_mode === "local" && (
                    <div className="r-mode-note">
                      <Icon name="spark" size={15} />
                      <span>
                        <strong>Local demo</strong> · Keyword retrieval and
                        rule-based drafts. No LLM or embedding API calls. Create
                        a Gemini workspace for model-backed support.
                      </span>
                    </div>
                  )}
                  {view === "overview" && (
                    <>
                      <div className="r-metrics">
                        {[
                          [
                            "Total tickets",
                            snapshot.metrics.tickets,
                            "inbox",
                            "Across this workspace",
                          ],
                          [
                            "Waiting for review",
                            snapshot.metrics.statuses.awaiting_approval ?? 0,
                            "shield",
                            "An admin’s next decision",
                          ],
                          [
                            "Ready documents",
                            snapshot.documents.filter(
                              (d) => d.status === "ready",
                            ).length,
                            "book",
                            "Sources your team can trust",
                          ],
                          [
                            "Recent model tokens",
                            snapshot.metrics.recent_tokens.toLocaleString(),
                            "pulse",
                            `Across the latest ${snapshot.metrics.recent_runs} runs`,
                          ],
                        ].map(([label, value, icon, hint]) => (
                          <article className="r-metric" key={String(label)}>
                            <span>
                              <span>{label}</span>
                              <Icon name={String(icon)} size={17} />
                            </span>
                            <strong>{value}</strong>
                            <small>{hint}</small>
                          </article>
                        ))}
                      </div>
                      <div className="r-overview-grid">
                        <section className="r-panel">
                          <div className="r-panel-heading">
                            <h2>Your support inbox</h2>
                            <button
                              className="r-text-button"
                              onClick={() => router.push("/tickets")}
                            >
                              View all <Icon name="arrow" size={14} />
                            </button>
                          </div>
                          {snapshot.tickets.length ? (
                            snapshot.tickets.slice(0, 5).map((t) => (
                              <button
                                className="r-ticket-row"
                                key={t.id}
                                onClick={() => void openTicket(t.id)}
                              >
                                <span className="r-ticket-avatar">
                                  {t.customer_email[0].toUpperCase()}
                                </span>
                                <span>
                                  <strong>{t.subject}</strong>
                                  <small>{t.customer_email}</small>
                                </span>
                                <Badge value={t.status} />
                                <Icon name="arrow" size={15} />
                              </button>
                            ))
                          ) : (
                            <Empty title="Your inbox starts here">
                              Create a ticket or load demo data to walk through
                              the complete workflow.
                            </Empty>
                          )}
                        </section>
                        <section className="r-panel">
                          <div className="r-panel-heading">
                            <h2>From question to resolution</h2>
                            <Icon name="spark" size={17} />
                          </div>
                          <ol className="r-workflow-steps">
                            {[
                              [
                                "Understand",
                                "Classify intent, priority and the right team.",
                              ],
                              [
                                "Find evidence",
                                "Retrieve policies and inspect an authorized order.",
                              ],
                              [
                                "Prepare a draft",
                                "Propose a reply and action with supporting sources.",
                              ],
                              [
                                "Review & resolve",
                                "An admin approves once. Every decision is recorded.",
                              ],
                            ].map(([title, description], i) => (
                              <li key={title}>
                                <span>{i + 1}</span>
                                <div>
                                  <strong>{title}</strong>
                                  <p>{description}</p>
                                </div>
                              </li>
                            ))}
                          </ol>
                          <p className="r-mini-note">
                            Average recent run:{" "}
                            {snapshot.metrics.average_duration_ms} ms · Failed
                            recent runs: {snapshot.metrics.failed_runs}
                          </p>
                        </section>
                      </div>
                      <section className="r-panel">
                        <div className="r-panel-heading">
                          <h2>Latest activity</h2>
                          <button
                            className="r-text-button"
                            onClick={() => router.push("/activity")}
                          >
                            See audit history <Icon name="arrow" size={14} />
                          </button>
                        </div>
                        {snapshot.audit.slice(0, 4).map((event) => (
                          <div className="r-event" key={event.id}>
                            <span className="r-event-dot" />
                            <div>
                              <strong>
                                {event.event
                                  .replaceAll(".", " · ")
                                  .replaceAll("_", " ")}
                              </strong>
                              <small>
                                {event.actor_id === user.id
                                  ? "You"
                                  : event.actor_id
                                    ? "Workspace teammate"
                                    : "Document worker"}
                              </small>
                            </div>
                            <time>{date(event.created_at)}</time>
                          </div>
                        ))}
                      </section>
                    </>
                  )}
                  {view === "tickets" && (
                    <div className="r-inbox-grid">
                      <section className="r-panel r-ticket-list">
                        <div className="r-inbox-controls">
                          <input
                            aria-label="Search tickets"
                            placeholder="Search subject or customer…"
                            value={search}
                            onChange={(event) => setSearch(event.target.value)}
                          />
                          <select
                            aria-label="Filter ticket status"
                            value={ticketFilter}
                            onChange={(event) =>
                              setTicketFilter(event.target.value)
                            }
                          >
                            <option value="all">All statuses</option>
                            {[
                              "open",
                              "processing",
                              "awaiting_approval",
                              "executed",
                              "rejected",
                              "escalated",
                            ].map((status) => (
                              <option key={status} value={status}>
                                {status.replaceAll("_", " ")}
                              </option>
                            ))}
                          </select>
                        </div>
                        {filteredTickets.map((t) => (
                          <button
                            key={t.id}
                            className={`r-inbox-item ${ticket?.id === t.id ? "r-selected" : ""}`}
                            onClick={() => void openTicket(t.id)}
                          >
                            <span>
                              <Badge value={t.priority} />
                              <time>{date(t.created_at)}</time>
                            </span>
                            <strong>{t.subject}</strong>
                            <small>{t.customer_email}</small>
                            <span>
                              <Badge value={t.status} />
                              <small>{t.queue}</small>
                            </span>
                          </button>
                        ))}
                        {!filteredTickets.length && (
                          <Empty title="No matching tickets">
                            Try another filter or create a customer request.
                          </Empty>
                        )}
                      </section>
                      <section className="r-panel r-ticket-detail">
                        {!ticket ? (
                          <Empty title="Every resolution starts with context">
                            Select a ticket to inspect its sources, order
                            lookup, and review history.
                          </Empty>
                        ) : (
                          <>
                            <div className="r-detail-heading">
                              <div>
                                <Badge value={ticket.status} />
                                <h2>{ticket.subject}</h2>
                                <p>
                                  {ticket.customer_email} · {ticket.queue} ·
                                  Revision {ticket.revision}
                                </p>
                              </div>
                              <button
                                className="r-button"
                                disabled={
                                  !!busy ||
                                  ticket.status === "executed" ||
                                  ticket.status === "processing"
                                }
                                onClick={() => void draftTicket()}
                              >
                                <Icon name="spark" size={15} />
                                {busy === "draft" ||
                                ticket.status === "processing"
                                  ? "Preparing…"
                                  : ticket.draft
                                    ? "Regenerate draft"
                                    : "Prepare draft"}
                              </button>
                            </div>
                            <div className="r-customer-message">
                              <span>CUSTOMER REQUEST</span>
                              <p>{ticket.description}</p>
                            </div>
                            {ticket.last_error && (
                              <p className="r-inline-error">
                                {ticket.last_error}
                              </p>
                            )}
                            {(busy === "draft" ||
                              ticket.status === "processing") && (
                              <div className="r-progress" role="status">
                                <span /> Classifying → retrieving policies →
                                looking up order → drafting
                              </div>
                            )}
                            {ticket.draft && (
                              <>
                                <div className="r-proposal">
                                  <Icon name="shield" />
                                  <div>
                                    <strong>
                                      Proposed action:{" "}
                                      {ticket.proposed_action.kind}
                                    </strong>
                                    <p>{ticket.proposed_action.rationale}</p>
                                    {ticket.proposed_action.amount_cents !==
                                      undefined && (
                                      <small>
                                        {ticket.proposed_action.reference} ·{" "}
                                        {money(
                                          ticket.proposed_action.amount_cents,
                                          ticket.proposed_action.currency ??
                                            "USD",
                                        )}{" "}
                                        · simulated
                                      </small>
                                    )}
                                  </div>
                                </div>
                                <label className="r-reply-label">
                                  Reply for review
                                  <textarea
                                    aria-label="Reply for review"
                                    rows={8}
                                    maxLength={12000}
                                    value={reply}
                                    disabled={
                                      !admin ||
                                      ticket.status !== "awaiting_approval" ||
                                      !!busy
                                    }
                                    onChange={(event) =>
                                      setReply(event.target.value)
                                    }
                                  />
                                </label>
                                {ticket.status === "awaiting_approval" &&
                                  (canReview ? (
                                    <div className="r-review-actions">
                                      <button
                                        className="r-button r-primary"
                                        disabled={!!busy || !reply.trim()}
                                        onClick={() => void review("approve")}
                                      >
                                        <Icon name="check" size={16} />
                                        {busy === "review"
                                          ? "Recording…"
                                          : "Approve & run mock action"}
                                      </button>
                                      <button
                                        className="r-button r-danger"
                                        disabled={!!busy || !reply.trim()}
                                        onClick={() => void review("reject")}
                                      >
                                        Reject proposal
                                      </button>
                                    </div>
                                  ) : (
                                    <p className="r-mini-note">
                                      An admin must review and approve this
                                      proposal.
                                    </p>
                                  ))}
                                <h3 className="r-section-title">
                                  Policy evidence
                                </h3>
                                {ticket.sources.length ? (
                                  <Sources sources={ticket.sources} />
                                ) : (
                                  <p className="r-mini-note">
                                    No policy passage was retrieved. Sensitive
                                    actions are escalated.
                                  </p>
                                )}
                                <div className="r-feedback">
                                  <span>Was this draft useful?</span>
                                  {[1, -1].map((vote) => (
                                    <button
                                      className="r-text-button"
                                      key={vote}
                                      disabled={!!busy}
                                      onClick={() =>
                                        void perform("feedback", async () => {
                                          await request(
                                            `${base}/tickets/${ticket.id}/feedback`,
                                            {
                                              method: "POST",
                                              body: JSON.stringify({ vote }),
                                            },
                                            token,
                                          );
                                          setNotice(
                                            "Feedback saved. Thank you.",
                                          );
                                          await refresh(workspaceId);
                                        })
                                      }
                                    >
                                      {vote === 1 ? "Helpful" : "Needs work"}
                                    </button>
                                  ))}
                                </div>
                              </>
                            )}
                            <details className="r-trace">
                              <summary>
                                Workflow trace & validated tool calls
                              </summary>
                              <p>
                                classify → retrieve → tools → draft → human
                                review
                              </p>
                              {ticket.tool_calls.map((tool, i) => (
                                <div key={i}>
                                  <strong>{tool.name}</strong>
                                  <pre>
                                    {JSON.stringify(
                                      {
                                        arguments: tool.arguments,
                                        result: tool.result,
                                      },
                                      null,
                                      2,
                                    )}
                                  </pre>
                                </div>
                              ))}
                              {ticket.runs.map((run) => (
                                <div className="r-run" key={run.id}>
                                  <Badge value={run.status} />
                                  <span>
                                    {run.model} · {run.prompt_version} ·{" "}
                                    {run.duration_ms} ms · {run.tokens} tokens
                                  </span>
                                  {run.error && <p>{run.error}</p>}
                                </div>
                              ))}
                            </details>
                            {!!ticket.decisions.length && (
                              <div className="r-history">
                                <h3>Review history</h3>
                                {ticket.decisions.map((d) => (
                                  <article key={d.id}>
                                    <Badge value={d.decision} />
                                    <span>{date(d.created_at)}</span>
                                    <p>{d.reply}</p>
                                    <small>
                                      {String(d.result.delivery)} · mock actions
                                      only
                                    </small>
                                  </article>
                                ))}
                              </div>
                            )}
                          </>
                        )}
                      </section>
                    </div>
                  )}
                  {view === "knowledge" && (
                    <div className="r-knowledge-grid">
                      <section className="r-panel">
                        <div className="r-panel-heading">
                          <h2>Company documents</h2>
                          <Badge value={`${snapshot.documents.length}_of_20`} />
                        </div>
                        <p className="r-mini-note">
                          {snapshot.workspace.ai_mode === "local"
                            ? "Local mode stores chunks in PostgreSQL and uses keyword retrieval."
                            : "Gemini embeds your documents into PostgreSQL with pgvector. Only matching passages from this workspace are used for answers."}{" "}
                          Original uploads stay in private document storage.
                        </p>
                        {admin && (
                          <>
                            <input
                              ref={fileInput}
                              className="r-file-input"
                              type="file"
                              aria-label="Upload company document"
                              accept=".pdf,.docx,.txt,.md"
                              onChange={(event) => {
                                const file = event.currentTarget.files?.[0];
                                if (file) void upload(file);
                                event.currentTarget.value = "";
                              }}
                            />
                            <button
                              className="r-upload-zone"
                              disabled={!!busy}
                              onClick={() => fileInput.current?.click()}
                            >
                              <span>
                                <Icon name="upload" size={25} />
                              </span>
                              <strong>
                                {busy === "upload"
                                  ? "Uploading…"
                                  : "Bring your company knowledge"}
                              </strong>
                              <small>
                                PDF, DOCX, TXT or Markdown · up to 20 MB
                              </small>
                            </button>
                          </>
                        )}
                        {pendingDocs && (
                          <p className="r-progress" role="status">
                            <span /> Document worker is processing the queue…
                          </p>
                        )}
                        {snapshot.documents.map((doc) => (
                          <article className="r-document" key={doc.id}>
                            <div>
                              <Icon name="book" />
                              <strong>{doc.filename}</strong>
                              <Badge value={doc.status} />
                            </div>
                            <p>
                              {doc.chunk_count} passages · attempt{" "}
                              {doc.attempts}/3
                            </p>
                            {doc.error && (
                              <p className="r-inline-error">{doc.error}</p>
                            )}
                            {admin && (
                              <footer>
                                {["ready", "failed"].includes(doc.status) && (
                                  <button
                                    className="r-text-button"
                                    disabled={!!busy}
                                    onClick={() =>
                                      void perform("reindex", async () => {
                                        await request(
                                          `${base}/documents/${doc.id}/reindex`,
                                          { method: "POST" },
                                          token,
                                        );
                                        await refresh(workspaceId);
                                      })
                                    }
                                  >
                                    Reindex document
                                  </button>
                                )}
                                {doc.status === "failed" &&
                                  doc.attempts < 3 && (
                                    <button
                                      className="r-text-button"
                                      disabled={!!busy}
                                      onClick={() =>
                                        void perform("retry", async () => {
                                          await request(
                                            `${base}/documents/${doc.id}/retry`,
                                            { method: "POST" },
                                            token,
                                          );
                                          await refresh(workspaceId);
                                        })
                                      }
                                    >
                                      Retry processing
                                    </button>
                                  )}
                                <button
                                  className="r-text-button r-danger-text"
                                  disabled={!!busy || doc.status === "deleting"}
                                  onClick={() => {
                                    if (
                                      window.confirm(
                                        `Remove ${doc.filename} and its searchable passages?`,
                                      )
                                    )
                                      void perform("remove", async () => {
                                        await request(
                                          `${base}/documents/${doc.id}`,
                                          { method: "DELETE" },
                                          token,
                                        );
                                        await refresh(workspaceId);
                                      });
                                  }}
                                >
                                  Remove document
                                </button>
                              </footer>
                            )}
                          </article>
                        ))}
                      </section>
                      <section className="r-panel">
                        <div className="r-panel-heading">
                          <h2>Company knowledge chat</h2>
                          <Icon name="spark" />
                        </div>
                        <div className="r-chat-toolbar">
                          <label>
                            Conversation
                            <select
                              aria-label="Knowledge conversation"
                              value={selectedThread}
                              disabled={!!busy}
                              onChange={(event) =>
                                setThreadId(event.target.value)
                              }
                            >
                              <option value="new">New conversation</option>
                              {snapshot.threads.map((thread) => (
                                <option key={thread.id} value={thread.id}>
                                  {thread.title}
                                </option>
                              ))}
                            </select>
                          </label>
                          <button
                            type="button"
                            className="r-button"
                            disabled={!!busy}
                            onClick={() => {
                              setThreadId("new");
                              setConversation({
                                workspace: workspaceId,
                                thread: "new",
                                answers: [],
                              });
                              setQuestion("");
                            }}
                          >
                            <Icon name="plus" size={15} /> New chat
                          </button>
                        </div>
                        <form className="r-question-form" onSubmit={ask}>
                          <label htmlFor={`${instanceId}-question`}>
                            Your question
                          </label>
                          <textarea
                            id={`${instanceId}-question`}
                            value={question}
                            onChange={(event) =>
                              setQuestion(event.target.value)
                            }
                            minLength={3}
                            maxLength={4000}
                            required
                            rows={3}
                            placeholder="What is our refund policy?"
                          />
                          <button
                            className="r-button r-primary"
                            disabled={!!busy || !question.trim()}
                          >
                            {busy === "ask"
                              ? "Finding evidence…"
                              : "Ask with sources"}
                            <Icon name="arrow" size={16} />
                          </button>
                        </form>
                        <div className="r-answers">
                          {[...answers].reverse().map((answer) => (
                            <article key={answer.id}>
                              <h3>{answer.question}</h3>
                              <Badge
                                value={
                                  answer.mode === "local"
                                    ? "local_demo"
                                    : "gemini"
                                }
                              />
                              <p>{answer.answer}</p>
                              <Sources sources={answer.sources} />
                              <time>{date(answer.created_at)}</time>
                            </article>
                          ))}
                          {!answers.length && (
                            <Empty title="Useful answers start with evidence">
                              Upload a policy, then ask a question. Every answer
                              keeps its supporting passages.
                            </Empty>
                          )}
                        </div>
                      </section>
                    </div>
                  )}
                  {view === "orders" && admin && (
                    <section className="r-panel">
                      <div className="r-panel-heading">
                        <div>
                          <h2>Mock order service</h2>
                          <p className="r-mini-note">
                            These records are demonstration data. No real
                            payments, shipments, emails, or refunds are issued.
                          </p>
                        </div>
                        {admin && (
                          <button
                            className="r-button"
                            disabled={!!busy}
                            onClick={() => setModal("order")}
                          >
                            <Icon name="plus" size={15} />
                            Add mock order
                          </button>
                        )}
                      </div>
                      <div className="r-table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>Order</th>
                              <th>Customer</th>
                              <th>Amount</th>
                              <th>Status</th>
                              <th>Eligibility</th>
                            </tr>
                          </thead>
                          <tbody>
                            {snapshot.orders.map((order) => (
                              <tr key={order.id}>
                                <td>
                                  <strong>{order.reference}</strong>
                                  <small>{order.item}</small>
                                </td>
                                <td>{order.customer_email}</td>
                                <td>
                                  {money(order.amount_cents, order.currency)}
                                </td>
                                <td>
                                  <Badge value={order.status} />
                                </td>
                                <td>
                                  {order.refundable ? "Refund review " : ""}
                                  {order.replaceable
                                    ? "Replacement review"
                                    : ""}
                                  {!order.refundable && !order.replaceable
                                    ? "No sensitive action"
                                    : ""}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {!snapshot.orders.length && (
                        <Empty title="A safe tool playground">
                          Load demo data or create a mock order to test order
                          lookup and approvals.
                        </Empty>
                      )}
                    </section>
                  )}
                  {view === "activity" && admin && (
                    <section className="r-panel">
                      <div className="r-panel-heading">
                        <h2>Workspace audit trail</h2>
                        <span className="r-mini-note">
                          Latest 100 events · read-only in the app
                        </span>
                      </div>
                      {snapshot.audit.map((event) => (
                        <details className="r-audit-event" key={event.id}>
                          <summary>
                            <span className="r-event-dot" />
                            <strong>
                              {event.event
                                .replaceAll(".", " · ")
                                .replaceAll("_", " ")}
                            </strong>
                            <span>
                              {event.actor_id === user.id
                                ? "You"
                                : event.actor_id
                                  ? "Teammate"
                                  : "Worker"}
                            </span>
                            <time>{date(event.created_at)}</time>
                          </summary>
                          <pre>{JSON.stringify(event.details, null, 2)}</pre>
                          {event.ticket_id && (
                            <button
                              className="r-text-button"
                              onClick={() => void openTicket(event.ticket_id!)}
                            >
                              Open related ticket{" "}
                              <Icon name="arrow" size={14} />
                            </button>
                          )}
                        </details>
                      ))}
                      {!snapshot.audit.length && (
                        <Empty title="A trace for every decision">
                          Workspace activity will appear here.
                        </Empty>
                      )}
                    </section>
                  )}
                  {!admin && ["orders", "activity"].includes(view) && (
                    <Empty title="Admin access required">
                      Ask a workspace owner or admin to access this page.
                    </Empty>
                  )}
                  {view === "team" && (
                    <TeamPanel
                      key={workspaceId}
                      workspace={snapshot.workspace}
                      user={user}
                    />
                  )}
                  {view === "settings" && (
                    <SettingsPanel
                      key={workspaceId}
                      workspace={snapshot.workspace}
                      onSaved={async () => {
                        await refresh(workspaceId);
                        setWorkspaces(
                          await request<Workspace[]>("/api/v1/workspaces"),
                        );
                      }}
                    />
                  )}
                </>
              )}
            </div>
            <footer className="r-page-footer">
              <span>ResolveAI · built for thoughtful support</span>
              <span>Evidence first. Human approved.</span>
            </footer>
          </section>
        </div>
      )}
      {modal && !authTransition && user && (
        <dialog
          className="r-dialog"
          ref={dialog}
          aria-labelledby={`${instanceId}-dialog`}
          onClose={() => setModal(null)}
        >
          <div className="r-dialog-heading">
            <h2 id={`${instanceId}-dialog`}>
              {modal === "workspace"
                ? "Create a workspace"
                : modal === "ticket"
                  ? "New support ticket"
                  : "Add mock order"}
            </h2>
            <button
              className="r-icon-button"
              aria-label="Close dialog"
              onClick={() => dialog.current?.close()}
            >
              <Icon name="close" />
            </button>
          </div>
          <form
            className="r-stacked-form"
            onSubmit={(event) => void createRecord(event, modal)}
          >
            {notice && (
              <p className="r-inline-error" role="alert">
                {notice}
              </p>
            )}
            {modal === "workspace" ? (
              <>
                <label>
                  Workspace name
                  <input
                    name="name"
                    minLength={2}
                    maxLength={100}
                    required
                    placeholder="Acme support"
                  />
                </label>
                <label>
                  AI mode
                  <select name="ai_mode">
                    <option value="local">Local demo · no provider keys</option>
                    <option value="gemini">Gemini + pgvector</option>
                  </select>
                </label>
                <p className="r-mini-note">
                  Local mode demonstrates the workflow using keyword retrieval
                  and rule-based drafts. Gemini mode requires your backend
                  Gemini and PostgreSQL configuration. Mode is fixed per
                  workspace.
                </p>
              </>
            ) : modal === "ticket" ? (
              <>
                <label>
                  Subject
                  <input
                    name="subject"
                    minLength={3}
                    maxLength={160}
                    required
                    placeholder="My order arrived damaged"
                  />
                </label>
                <label>
                  Customer email
                  <input
                    name="customer_email"
                    type="email"
                    required
                    placeholder="customer@example.com"
                  />
                </label>
                <label>
                  Order reference (optional)
                  <input
                    name="order_reference"
                    maxLength={40}
                    placeholder="ORD-1002"
                  />
                </label>
                <label>
                  Customer message
                  <textarea
                    name="description"
                    minLength={5}
                    maxLength={12000}
                    required
                    rows={5}
                    placeholder="Describe the customer’s request…"
                  />
                </label>
              </>
            ) : (
              <>
                <label>
                  Order reference
                  <input
                    name="reference"
                    pattern="[A-Za-z0-9][A-Za-z0-9-]{2,39}"
                    required
                    placeholder="ORD-1004"
                  />
                </label>
                <label>
                  Customer email
                  <input name="customer_email" type="email" required />
                </label>
                <label>
                  Item
                  <input name="item" maxLength={160} required />
                </label>
                <div className="r-form-row">
                  <label>
                    Amount (USD)
                    <input
                      name="amount"
                      type="number"
                      min="0.01"
                      max="100000"
                      step="0.01"
                      required
                    />
                  </label>
                  <label>
                    Status
                    <select name="status">
                      <option value="processing">Processing</option>
                      <option value="shipped">Shipped</option>
                      <option value="delivered">Delivered</option>
                    </select>
                  </label>
                </div>
                <label className="r-checkbox">
                  <input name="refundable" type="checkbox" />
                  Eligible for refund review
                </label>
                <label className="r-checkbox">
                  <input name="replaceable" type="checkbox" />
                  Eligible for replacement review
                </label>
                <p className="r-mini-note">
                  These flags are server-checked again when an admin approves an
                  action.
                </p>
              </>
            )}
            <button className="r-button r-primary" disabled={!!busy}>
              {busy === "create" ? "Saving…" : "Create"}
              <Icon name="arrow" size={16} />
            </button>
          </form>
        </dialog>
      )}
    </main>
  );
}
