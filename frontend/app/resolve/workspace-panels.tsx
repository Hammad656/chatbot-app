"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { request, acceptSession } from "./api";
import type { User, Workspace, AuthSession } from "./types";
import { CustomerPortalSettings } from "./customer-panels";

function workspaceRequest<T>(
  workspaceId: string,
  path: string,
  options: RequestInit = {},
) {
  const headers = new Headers(options.headers);
  headers.set("X-Workspace-ID", workspaceId);
  return request<T>(path, { ...options, headers });
}

type Member = {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: "owner" | "admin" | "agent";
};
type Invitation = {
  id: string;
  email: string;
  role: string;
  status: string;
  expires_at: string;
};
type Settings = {
  id: string;
  name: string;
  slug: string;
  ai_mode: string;
  embedding_model: string;
  allowed_origins: string[];
  widget_public_key: string | null;
  settings: {
    tone: "professional" | "friendly" | "concise";
    similarity_threshold: number;
    widget_enabled: boolean;
    order_provider: "internal" | "external";
  };
};

export function TeamPanel({
  workspace,
  user,
}: {
  workspace: Workspace;
  user: User;
}) {
  const admin = workspace.role === "owner" || workspace.role === "admin";
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [link, setLink] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const [people, invitations] = await Promise.all([
      workspaceRequest<Member[]>(workspace.id, "/api/v1/members"),
      workspaceRequest<Invitation[]>(workspace.id, "/api/v1/invitations"),
    ]);
    setMembers(people);
    setInvites(invitations);
  }, [workspace.id]);
  useEffect(() => {
    if (!admin) return;
    let active = true;
    void Promise.all([
      workspaceRequest<Member[]>(workspace.id, "/api/v1/members"),
      workspaceRequest<Invitation[]>(workspace.id, "/api/v1/invitations"),
    ])
      .then(([people, invitations]) => {
        if (active) {
          setMembers(people);
          setInvites(invitations);
        }
      })
      .catch((error) => {
        if (active) setNotice(error.message);
      });
    return () => {
      active = false;
    };
  }, [admin, workspace.id]);
  async function change(operation: () => Promise<void>) {
    setBusy(true);
    setNotice("");
    try {
      await operation();
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The update failed.");
    } finally {
      setBusy(false);
    }
  }
  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await change(async () => {
      const invitation = await workspaceRequest<{ invitation_token: string }>(
        workspace.id,
        "/api/v1/members/invite",
        {
          method: "POST",
          body: JSON.stringify({
            email: data.get("email"),
            role: data.get("role"),
          }),
        },
      );
      setLink(
        `${window.location.origin}/invite#${invitation.invitation_token}`,
      );
      form.reset();
    });
  }
  return (
    <div className="r-team-settings">
      {notice && (
        <p className="r-inline-error" role="alert">
          {notice}
        </p>
      )}
      <div className="r-knowledge-grid">
        <section className="r-panel">
          <div className="r-panel-heading">
            <h2>Workspace members</h2>
          </div>
          {admin ? (
            members.map((member) => (
              <div className="r-team-member" key={member.id}>
                <div>
                  <strong>{member.full_name || member.email}</strong>
                  <p className="r-mini-note">
                    {member.email}
                    {member.user_id === user.id ? " · You" : ""}
                  </p>
                </div>
                {member.role === "owner" || member.user_id === user.id ? (
                  <span className="r-badge">{member.role}</span>
                ) : (
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      const data = new FormData(event.currentTarget);
                      void change(async () => {
                        await workspaceRequest(
                          workspace.id,
                          `/api/v1/members/${member.id}`,
                          {
                            method: "PATCH",
                            body: JSON.stringify({ role: data.get("role") }),
                          },
                        );
                      });
                    }}
                  >
                    <select
                      name="role"
                      aria-label={`Role for ${member.email}`}
                      defaultValue={member.role}
                      disabled={busy}
                    >
                      <option value="agent">Agent</option>
                      <option value="admin">Admin</option>
                    </select>
                    <button className="r-button" disabled={busy}>
                      Save role
                    </button>
                    <button
                      className="r-text-button r-danger"
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void change(async () => {
                          await workspaceRequest(
                            workspace.id,
                            `/api/v1/members/${member.id}`,
                            {
                              method: "DELETE",
                            },
                          );
                        })
                      }
                    >
                      Remove
                    </button>
                  </form>
                )}
              </div>
            ))
          ) : (
            <p>Ask a workspace owner or admin to manage membership.</p>
          )}
          <div className="r-role-guide">
            <h3>Owner</h3>
            <p>
              Owns this workspace and has all administrator permissions. The
              owner cannot be removed or demoted.
            </p>
            <h3>Admin</h3>
            <p>
              Manages company documents, settings and the team. Approves
              sensitive actions.
            </p>
            <h3>Agent</h3>
            <p>
              Works tickets, reads company knowledge and reviews low-risk
              replies. Cannot manage members or approve refunds.
            </p>
          </div>
        </section>
        <section className="r-panel">
          <div className="r-panel-heading">
            <h2>Invite a teammate</h2>
          </div>
          {admin ? (
            <>
              <form className="r-stacked-form" onSubmit={invite}>
                <label>
                  Teammate email
                  <input
                    name="email"
                    type="email"
                    autoComplete="off"
                    required
                    placeholder="teammate@company.com"
                  />
                </label>
                <label>
                  Workspace role
                  <select name="role">
                    <option value="agent">Agent</option>
                    <option value="admin">Admin</option>
                  </select>
                </label>
                <p className="r-mini-note">
                  Create a link to share with this teammate. They can sign up
                  before accepting it. Links expire after seven days; no email
                  is sent automatically.
                </p>
                <button className="r-button r-primary" disabled={busy}>
                  {busy ? "Saving…" : "Create invitation"}
                </button>
              </form>
              {link && (
                <div className="r-invite-link">
                  <label>
                    Your invitation link
                    <textarea
                      aria-label="Invitation link"
                      readOnly
                      value={link}
                      rows={3}
                    />
                  </label>
                  <button
                    className="r-button"
                    onClick={() => {
                      void navigator.clipboard
                        .writeText(link)
                        .then(() => setNotice("Invitation link copied."))
                        .catch(() =>
                          setNotice("Select the invitation link and copy it."),
                        );
                    }}
                  >
                    Copy invitation link
                  </button>
                </div>
              )}
            </>
          ) : (
            <p>Only owners and admins can invite teammates.</p>
          )}
        </section>
      </div>
      {admin && (
        <section className="r-panel">
          <h2>Invitations</h2>
          {!invites.length && (
            <p className="r-mini-note">Your invitations will appear here.</p>
          )}
          {invites.map((invitation) => (
            <div className="r-team-member" key={invitation.id}>
              <div>
                <strong>{invitation.email}</strong>
                <p className="r-mini-note">
                  {invitation.role} · {invitation.status}
                </p>
              </div>
              {invitation.status === "pending" && (
                <button
                  className="r-button"
                  disabled={busy}
                  onClick={() =>
                    void change(async () => {
                      await workspaceRequest(
                        workspace.id,
                        `/api/v1/invitations/${invitation.id}`,
                        {
                          method: "DELETE",
                        },
                      );
                    })
                  }
                >
                  Revoke invitation
                </button>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

export function SettingsPanel({
  workspace,
  onSaved,
}: {
  workspace: Workspace;
  onSaved: () => Promise<void>;
}) {
  const [data, setData] = useState<Settings | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const admin = workspace.role === "owner" || workspace.role === "admin";
  useEffect(() => {
    let active = true;
    void workspaceRequest<Settings>(workspace.id, "/api/v1/workspace/settings")
      .then((value) => {
        if (active) setData(value);
      })
      .catch((error) => {
        if (active) setNotice(error.message);
      });
    return () => {
      active = false;
    };
  }, [workspace.id]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!data) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice("");
    try {
      setData(
        await workspaceRequest<Settings>(
          workspace.id,
          "/api/v1/workspace/settings",
          {
            method: "PATCH",
            body: JSON.stringify({
              name: form.get("name"),
              allowed_origins: String(form.get("allowed_origins") ?? "")
                .split(/\r?\n/)
                .map((value) => value.trim())
                .filter(Boolean),
              settings: {
                ...data.settings,
                tone: form.get("tone") ?? data.settings.tone,
                similarity_threshold: Number(
                  form.get("similarity_threshold") ??
                    data.settings.similarity_threshold,
                ),
                widget_enabled: form.get("widget_enabled") === "on",
                order_provider: form.get("order_provider"),
              },
            }),
          },
        ),
      );
      await onSaved();
      setNotice("Workspace settings saved.");
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Could not save settings.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function removeWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setNotice("");
    try {
      const session = await workspaceRequest<AuthSession>(
        workspace.id,
        `/api/v1/workspaces/${workspace.id}`,
        {
          method: "DELETE",
          body: JSON.stringify({
            confirmation_name: form.get("confirmation_name"),
          }),
        },
      );
      acceptSession(session);
      window.location.replace(
        session.user.active_workspace_id ? "/knowledge" : "/onboarding",
      );
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Could not delete workspace.",
      );
      setBusy(false);
    }
  }
  return (
    <section className="r-panel r-settings-panel">
      <div className="r-panel-heading">
        <h2>Workspace settings</h2>
      </div>
      {notice && (
        <p role="status" className="r-mini-note">
          {notice}
        </p>
      )}
      {data ? (
        <form className="r-stacked-form" onSubmit={save}>
          <label>
            Company workspace name
            <input
              name="name"
              required
              minLength={2}
              maxLength={100}
              defaultValue={data.name}
              disabled={!admin}
            />
          </label>
          <label>
            Answer tone
            <select
              name="tone"
              defaultValue={data.settings.tone}
              disabled={!admin || data.ai_mode === "local"}
            >
              <option value="professional">Professional</option>
              <option value="friendly">Friendly</option>
              <option value="concise">Concise</option>
            </select>
          </label>
          <label>
            Minimum document similarity
            <input
              name="similarity_threshold"
              type="number"
              min="0"
              max="1"
              step="0.05"
              defaultValue={data.settings.similarity_threshold}
              disabled={!admin || data.ai_mode === "local"}
            />
          </label>
          <p className="r-mini-note">
            Gemini uses these answer settings. Local demo mode returns matching
            excerpts. A higher similarity value requires a closer match between
            the question and your company documents. If there is no supporting
            passage, the assistant asks for more information.
          </p>
          <label className="r-checkbox-label">
            <input
              type="checkbox"
              name="widget_enabled"
              defaultChecked={data.settings.widget_enabled}
              disabled={!admin}
            />
            Enable customer chat for this company
          </label>
          <label>
            Allowed website origins
            <textarea
              name="allowed_origins"
              rows={3}
              defaultValue={data.allowed_origins.join("\n")}
              disabled={!admin}
              placeholder={
                "https://your-company.com\nhttps://shop.your-company.com"
              }
            />
            <small>
              One exact origin per line. Your own dashboard origin is always
              supported. Other sites can embed the widget only after being added
              here.
            </small>
          </label>
          <label>
            Order data source
            <select
              name="order_provider"
              defaultValue={data.settings.order_provider}
              disabled={!admin}
            >
              <option value="internal">
                Built-in authorized order service
              </option>
              <option value="external">Configured external order API</option>
            </select>
            <small>
              The external option requires ORDER_API_BASE_URL and
              ORDER_API_TOKEN on the server. Credentials never enter the
              browser.
            </small>
          </label>
          {admin && (
            <button className="r-button r-primary" disabled={busy}>
              {busy ? "Saving…" : "Save settings"}
            </button>
          )}
          <p className="r-mini-note">
            Your role: {workspace.role}. Changes apply only to this workspace.
          </p>
        </form>
      ) : (
        <p>Loading settings…</p>
      )}
      {data && admin && (
        <CustomerPortalSettings
          key={workspace.id}
          workspaceId={workspace.id}
          widgetKey={data.widget_public_key}
          enabled={data.settings.widget_enabled}
        />
      )}
      {data && workspace.role === "owner" && (
        <section className="r-danger-zone">
          <h3>Delete workspace</h3>
          <p>
            Company documents, conversations, tickets and memberships in this
            workspace will be permanently removed. Your account and other
            workspaces remain.
          </p>
          <form className="r-stacked-form" onSubmit={removeWorkspace}>
            <label>
              Type <strong>{data.name}</strong> to confirm
              <input
                name="confirmation_name"
                required
                autoComplete="off"
                minLength={2}
                maxLength={100}
              />
            </label>
            <button className="r-button r-danger" disabled={busy}>
              Delete this workspace
            </button>
          </form>
        </section>
      )}
    </section>
  );
}
