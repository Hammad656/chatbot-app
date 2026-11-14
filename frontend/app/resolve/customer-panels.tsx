"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { request } from "./api";
import type { TicketDetail, Workspace, User, Source } from "./types";

function scoped<T>(workspace: string, path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  headers.set("X-Workspace-ID", workspace);
  return request<T>(path, { ...options, headers });
}
type Page<T> = { items: T[]; next_cursor: string | null };
type Conversation = {
  id: string;
  title: string;
  status: string;
  created_at: string;
};
type ConversationDetail = Conversation & {
  contact: { name: string; email: string | null };
  ticket_ids: string[];
  messages: { id: string; role: string; content: string; sources: Source[] }[];
};
type Action = {
  id: string;
  ticket_id: string;
  kind: string;
  risk: string;
  status: string;
  reply: string;
  error: string | null;
};

export function CustomerConversationsPanel({
  workspace,
}: {
  workspace: Workspace;
}) {
  const [rows, setRows] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<ConversationDetail | null>(null);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const load = () =>
      void scoped<Page<Conversation>>(
        workspace.id,
        "/api/v1/customer-conversations",
      )
        .then((data) => {
          if (active) {
            setRows(data.items);
            setLoading(false);
          }
        })
        .catch((error) => {
          if (active) {
            setNotice(error.message);
            setLoading(false);
          }
        });
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [workspace.id]);
  async function open(id: string) {
    try {
      setSelected(
        await scoped<ConversationDetail>(
          workspace.id,
          `/api/v1/customer-conversations/${id}`,
        ),
      );
      setNotice("");
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Could not open conversation.",
      );
    }
  }
  return (
    <div className="r-knowledge-grid">
      <section className="r-panel">
        <div className="r-panel-heading">
          <h2>Customer conversations</h2>
          <span className="r-badge">{rows.length} recent</span>
        </div>
        {notice && (
          <p className="r-inline-error" role="alert">
            {notice}
          </p>
        )}
        {loading ? (
          <p>Loading conversations…</p>
        ) : !rows.length ? (
          <div className="r-empty">
            <span>✦</span>
            <h3>Every customer conversation, together.</h3>
            <p>
              Enable customer chat in Settings and share your support link.
              Conversations appear here as customers write.
            </p>
            <Link className="r-button" href="/settings">
              Set up customer chat →
            </Link>
          </div>
        ) : (
          rows.map((row) => (
            <button
              className={`r-conversation-row ${selected?.id === row.id ? "r-selected" : ""}`}
              key={row.id}
              onClick={() => void open(row.id)}
            >
              <span>
                <strong>{row.title}</strong>
                <small>{new Date(row.created_at).toLocaleString()}</small>
              </span>
              <span className={`r-badge r-${row.status.replaceAll("_", "-")}`}>
                {row.status.replaceAll("_", " ")}
              </span>
            </button>
          ))
        )}
      </section>
      <section className="r-panel">
        {selected ? (
          <>
            <div className="r-panel-heading">
              <div>
                <h2>{selected.contact.name}</h2>
                <p className="r-mini-note">
                  {selected.contact.email ?? "Guest customer"}
                </p>
              </div>
              <button
                className="r-button"
                onClick={() => void open(selected.id)}
              >
                Refresh
              </button>
            </div>
            {selected.messages.map((message) => (
              <article
                className={`r-conversation-message r-message-${message.role}`}
                key={message.id}
              >
                <span>
                  {message.role === "agent"
                    ? "Support agent · Approved reply"
                    : message.role}
                </span>
                <p>{message.content}</p>
                {message.sources.length > 0 && (
                  <small>{message.sources.length} supporting source(s)</small>
                )}
              </article>
            ))}
            {selected.ticket_ids.map((id) => (
              <Link className="r-button" href={`/tickets/${id}`} key={id}>
                Open support ticket {id.slice(0, 8).toUpperCase()} →
              </Link>
            ))}
          </>
        ) : (
          <div className="r-empty">
            <span>↗</span>
            <h3>Follow the whole conversation.</h3>
            <p>
              View customer questions, grounded answers, handoffs and the
              replies your team approved.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

export function ApprovalsPanel({ workspace }: { workspace: Workspace }) {
  const [rows, setRows] = useState<Action[]>([]);
  const [status, setStatus] = useState("proposed");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true;
    const load = () =>
      void scoped<Page<Action>>(
        workspace.id,
        `/api/v1/actions${status ? `?status=${status}` : ""}`,
      )
        .then((data) => {
          if (active) setRows(data.items);
        })
        .catch((error) => {
          if (active) setNotice(error.message);
        });
    load();
    const timer = window.setInterval(load, 4000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [workspace.id, status]);
  return (
    <section className="r-panel">
      <div className="r-panel-heading">
        <div>
          <h2>Human approval queue</h2>
          <p className="r-mini-note">
            Read the evidence and proposed reply before approving. Sensitive
            actions require an owner or admin.
          </p>
        </div>
        <label>
          Action status
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="proposed">Needs review</option>
            <option value="approved">Queued for delivery</option>
            <option value="succeeded">Completed</option>
            <option value="failed">Needs attention</option>
            <option value="">All actions</option>
          </select>
        </label>
      </div>
      {notice && (
        <p role="alert" className="r-inline-error">
          {notice}
        </p>
      )}
      {!rows.length ? (
        <div className="r-empty">
          <span>✓</span>
          <h3>Nothing waiting in this view.</h3>
          <p>
            Prepared support tickets appear here with their proposed action and
            review context.
          </p>
        </div>
      ) : (
        <div className="r-approval-grid">
          {rows.map((row) => (
            <article className="r-approval-card" key={row.id}>
              <div>
                <span className={`r-badge r-${row.status}`}>{row.status}</span>
                <span className={`r-badge r-${row.risk}`}>
                  {row.risk === "sensitive"
                    ? "Admin approval"
                    : "Agent approval"}
                </span>
              </div>
              <h3>
                {row.kind === "reply"
                  ? "Send a reviewed reply"
                  : row.kind === "escalate"
                    ? "Human support handoff"
                    : `Simulated ${row.kind}`}
              </h3>
              <p>
                {row.reply.slice(0, 260)}
                {row.reply.length > 260 ? "…" : ""}
              </p>
              {row.error && <p className="r-inline-error">{row.error}</p>}
              <Link
                className="r-button r-primary"
                href={`/tickets/${row.ticket_id}`}
              >
                {row.status === "proposed" ? "Review ticket" : "View outcome"} →
              </Link>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export function CustomerPortalSettings({
  workspaceId,
  widgetKey,
  enabled,
}: {
  workspaceId: string;
  widgetKey: string | null;
  enabled: boolean;
}) {
  const [link, setLink] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<
    {
      id: string;
      name: string;
      email: string;
      revoked: boolean;
      expires_at: string;
    }[]
  >([]);
  const load = useCallback(
    async () =>
      setRows(await scoped(workspaceId, "/api/v1/customer-access-links")),
    [workspaceId],
  );
  useEffect(() => {
    let active = true;
    void scoped<typeof rows>(workspaceId, "/api/v1/customer-access-links")
      .then((value) => {
        if (active) setRows(value);
      })
      .catch((error) => {
        if (active) setNotice(error.message);
      });
    return () => {
      active = false;
    };
  }, [workspaceId]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!widgetKey) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice("");
    try {
      const result = await scoped<{ access_token: string }>(
        workspaceId,
        "/api/v1/customer-access-links",
        {
          method: "POST",
          body: JSON.stringify({
            name: form.get("customer_name"),
            email: form.get("customer_email"),
          }),
        },
      );
      setLink(
        `${window.location.origin}/widget/${widgetKey}#access=${result.access_token}`,
      );
      await load();
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Could not create customer access.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice("Copied to clipboard.");
    } catch {
      setNotice("Select the text and copy it manually.");
    }
  }
  return (
    <section className="r-portal-setup">
      <div className="r-panel-heading">
        <div>
          <h2>Customer support portal</h2>
          <p className="r-mini-note">
            Company policy chat, private order support and replies from your
            team.
          </p>
        </div>
        <span className={`r-badge ${enabled ? "r-ready" : ""}`}>
          {enabled ? "Chat is enabled" : "Chat is disabled"}
        </span>
      </div>
      {notice && (
        <p className="r-mini-note" role="status">
          {notice}
        </p>
      )}
      {widgetKey && (
        <div className="r-portal-launch">
          <div>
            <strong>One support link for your customers</strong>
            <p>
              Guest visitors can ask about company documents. Private access
              links authorize order questions.
            </p>
          </div>
          {enabled && (
            <Link
              className="r-button r-primary"
              target="_blank"
              href={`/widget/${widgetKey}`}
            >
              Open customer chat ↗
            </Link>
          )}
          <button
            type="button"
            className="r-button"
            disabled={!enabled}
            onClick={() =>
              void copy(`${window.location.origin}/widget/${widgetKey}`)
            }
          >
            Copy support link
          </button>
          <button
            type="button"
            className="r-button"
            disabled={!enabled}
            onClick={() =>
              void copy(
                `<script src="${window.location.origin}/widget.js" data-workspace="${widgetKey}" defer></script>`,
              )
            }
          >
            Copy embed snippet
          </button>
        </div>
      )}
      <p className="r-mini-note">
        Enable chat and save allowed website origins above before embedding. The
        iframe keeps customer sessions separate from the host website.
      </p>
      <div className="r-knowledge-grid">
        <form className="r-stacked-form" onSubmit={create}>
          <h3>Give a customer private order access</h3>
          <label>
            Customer name
            <input
              name="customer_name"
              required
              maxLength={100}
              placeholder="Alex Morgan"
            />
          </label>
          <label>
            Customer email
            <input
              name="customer_email"
              type="email"
              required
              placeholder="alex@example.com"
            />
          </label>
          <p className="r-mini-note">
            Use the email recorded on their order. Share the private link
            through a trusted channel; no email is sent automatically. Creating
            a new link revokes previous links for this customer.
          </p>
          <button className="r-button r-primary" disabled={busy || !enabled}>
            {busy ? "Creating…" : "Create private access link"}
          </button>
          {link && (
            <>
              <label>
                Private customer link
                <input
                  readOnly
                  value={link}
                  onFocus={(event) => event.target.select()}
                />
              </label>
              <button
                className="r-button"
                type="button"
                onClick={() => void copy(link)}
              >
                Copy private link
              </button>
            </>
          )}
        </form>
        <div>
          <h3>Customer access links</h3>
          {!rows.length && (
            <p className="r-mini-note">No private links issued yet.</p>
          )}
          {rows.map((row) => (
            <div className="r-customer-link-row" key={row.id}>
              <div>
                <strong>{row.name}</strong>
                <small>{row.email}</small>
                <small>
                  {row.revoked
                    ? "Revoked"
                    : `Expires ${new Date(row.expires_at).toLocaleDateString()}`}
                </small>
              </div>
              {!row.revoked && (
                <button
                  type="button"
                  className="r-button r-danger"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void scoped(
                      workspaceId,
                      `/api/v1/customer-access-links/${row.id}`,
                      { method: "DELETE" },
                    )
                      .then(load)
                      .catch((error) => setNotice(error.message))
                      .finally(() => setBusy(false));
                  }}
                >
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function TicketControls({
  ticket,
  workspace,
  user,
  onSaved,
}: {
  ticket: TicketDetail;
  workspace: Workspace;
  user: User;
  onSaved: () => Promise<void>;
}) {
  const [members, setMembers] = useState<
    { user_id: string; email: string; full_name: string }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!["admin", "owner"].includes(workspace.role)) return;
    let active = true;
    void scoped<typeof members>(workspace.id, "/api/v1/members")
      .then((data) => {
        if (active) setMembers(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [workspace.id, workspace.role]);
  async function update(body: Record<string, unknown>) {
    setBusy(true);
    setNotice("");
    try {
      await scoped(workspace.id, `/api/v1/tickets/${ticket.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      await onSaved();
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Could not update ticket.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="r-ticket-controls">
      <label>
        Priority
        <select
          value={ticket.priority}
          disabled={busy}
          onChange={(event) => void update({ priority: event.target.value })}
        >
          {["low", "normal", "high", "urgent"].map((value) => (
            <option value={value} key={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label>
        Assigned to
        <select
          value={ticket.assigned_to ?? ""}
          disabled={busy}
          onChange={(event) =>
            void update({ assigned_to: event.target.value || null })
          }
        >
          <option value="">Unassigned</option>
          {!members.some((member) => member.user_id === user.id) && (
            <option value={user.id}>Me</option>
          )}
          {members.map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.full_name || member.email}
            </option>
          ))}
        </select>
      </label>
      <button
        className="r-button"
        disabled={
          busy || ["processing", "delivery_pending"].includes(ticket.status)
        }
        onClick={() =>
          void update({
            status: ticket.status === "resolved" ? "open" : "resolved",
          })
        }
      >
        {ticket.status === "resolved" ? "Reopen ticket" : "Mark resolved"}
      </button>
      {notice && (
        <p className="r-inline-error" role="alert">
          {notice}
        </p>
      )}
    </div>
  );
}
