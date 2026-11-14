"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import type { Source } from "../resolve/types";
import "../resolve/support.css";
import "./widget.css";
import {
  customerIdentity,
  publishCustomer,
  subscribeCustomer,
} from "./session";

type Session = {
  access_token: string;
  conversation_id: string;
  verified: boolean;
  name: string;
  company_name: string;
  email: string | null;
};
type Message = { id: string; role: string; content: string; sources: Source[] };
type Conversation = {
  status: string;
  messages: Message[];
  processing: boolean;
};
class CustomerAPIError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function decode<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new CustomerAPIError(
      response.status,
      body?.error?.message ?? `Support request failed (${response.status}).`,
    );
  return body as T;
}

export default function CustomerChat() {
  const { workspaceKey } = useParams<{ workspaceKey: string }>();
  const query = useSearchParams();
  const [storedSession, setSession] = useState<Session | null>(null);
  const activeIdentity = useSyncExternalStore(
    subscribeCustomer,
    () => customerIdentity(workspaceKey),
    () => null,
  );
  const session =
    storedSession && storedSession.conversation_id === activeIdentity
      ? storedSession
      : null;
  const [conversation, setConversation] = useState<Conversation>({
    status: "active",
    messages: [],
    processing: false,
  });
  const [text, setText] = useState("");
  const [access, setAccess] = useState("");
  const [notice, setNotice] = useState("");
  const [opening, setOpening] = useState(true);
  const [busy, setBusy] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [streamed, setStreamed] = useState("");
  const [votes, setVotes] = useState<Record<string, number>>({});
  const stream = useRef<AbortController | null>(null);
  const live = useRef<Session | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const broadcast = useRef<BroadcastChannel | null>(null);
  const siteOrigin = query.get("site_origin");
  const embedded = query.get("embedded") === "1";
  const refresh = useCallback(
    async (current: Session) => {
      try {
        const data = await decode<Conversation>(
          await fetch(
            `/api/v1/widget/conversations/${current.conversation_id}`,
            {
              headers: { Authorization: `Bearer ${current.access_token}` },
              cache: "no-store",
            },
          ),
        );
        if (live.current === current) setConversation(data);
      } catch (error) {
        if (
          error instanceof CustomerAPIError &&
          error.status === 401 &&
          live.current === current
        ) {
          publishCustomer(workspaceKey, null);
          live.current = null;
          stream.current?.abort();
          setSession(null);
          setConversation({
            status: "active",
            messages: [],
            processing: false,
          });
          setSignedOut(true);
        }
        throw error;
      }
    },
    [workspaceKey],
  );
  const open = useCallback(
    async (code?: string) => {
      setOpening(true);
      setNotice("");
      try {
        const operation = async () =>
          decode<Session>(
            await fetch("/api/v1/widget/session", {
              method: "POST",
              credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                workspace_key: workspaceKey,
                access_token: code || undefined,
                site_origin: siteOrigin || undefined,
              }),
            }),
          );
        const current = navigator.locks
          ? await navigator.locks.request(
              `resolveai-customer-${workspaceKey}`,
              operation,
            )
          : await operation();
        live.current = current;
        publishCustomer(workspaceKey, current.conversation_id);
        if (code)
          broadcast.current?.postMessage({
            kind: "identity",
            conversation: current.conversation_id,
          });
        setSession(current);
        setSignedOut(false);
        setAccess("");
        await refresh(current);
      } catch (error) {
        setNotice(
          error instanceof Error
            ? error.message
            : "Cannot reach customer support.",
        );
      } finally {
        setOpening(false);
      }
    },
    [workspaceKey, siteOrigin, refresh],
  );
  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const code = fragment.get("access") || "";
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      if (code)
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      void open(code);
    });
    return () => {
      active = false;
      stream.current?.abort();
      live.current = null;
    };
  }, [open]);
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(`resolveai-customer-${workspaceKey}`);
    broadcast.current = channel;
    channel.onmessage = (event) => {
      if (
        event.data?.kind === "logout" ||
        (event.data?.kind === "identity" &&
          event.data.conversation !== live.current?.conversation_id)
      ) {
        publishCustomer(workspaceKey, null);
        live.current = null;
        stream.current?.abort();
        stream.current = null;
        setSession(null);
        setConversation({ status: "active", messages: [], processing: false });
        setSignedOut(true);
        setBusy(false);
      }
    };
    return () => {
      broadcast.current = null;
      channel.close();
    };
  }, [workspaceKey]);
  useEffect(() => {
    if (!session) return;
    const timer = window.setInterval(() => {
      if (!stream.current)
        void refresh(session).catch((error) => setNotice(error.message));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [session, refresh]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "instant", block: "nearest" });
  }, [conversation.messages.length, streamed]);
  async function logout() {
    if (!session) return;
    setBusy(true);
    try {
      await decode(
        await fetch("/api/v1/widget/logout", {
          method: "POST",
          credentials: "include",
          headers: { Authorization: `Bearer ${session.access_token}` },
        }),
      );
      stream.current?.abort();
      stream.current = null;
      live.current = null;
      publishCustomer(workspaceKey, null);
      broadcast.current?.postMessage({ kind: "logout" });
      setSession(null);
      setConversation({ status: "active", messages: [], processing: false });
      setSignedOut(true);
      setNotice("");
      setText("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not log out.");
    } finally {
      setBusy(false);
    }
  }
  async function send(event?: FormEvent<HTMLFormElement>, suggestion?: string) {
    event?.preventDefault();
    const content = (suggestion ?? text).trim();
    if (!session || !content || busy || conversation.processing) return;
    const current = session;
    setBusy(true);
    setNotice("");
    setText("");
    setStreamed("");
    setConversation((previous) => ({
      ...previous,
      messages: [
        ...previous.messages,
        { id: crypto.randomUUID(), role: "customer", content, sources: [] },
      ],
    }));
    const controller = new AbortController();
    stream.current = controller;
    try {
      const response = await fetch("/api/v1/widget/messages", {
        method: "POST",
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${current.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ content, request_id: crypto.randomUUID() }),
      });
      if (!response.ok) {
        await decode(response);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader)
        throw new Error("Your browser could not open the support stream.");
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        buffer += decoder.decode(next.value, { stream: true });
        let end: number;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const packet = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          const name = packet
            .split("\n")
            .find((line) => line.startsWith("event: "))
            ?.slice(7);
          const raw = packet
            .split("\n")
            .find((line) => line.startsWith("data: "))
            ?.slice(6);
          if (!raw) continue;
          const data = JSON.parse(raw);
          if (name === "delta") setStreamed((previous) => previous + data.text);
          if (name === "error") setNotice(data.message);
        }
      }
    } catch (error) {
      if (!controller.signal.aborted)
        setNotice(
          error instanceof Error
            ? error.message
            : "Message delivery was interrupted. Your conversation keeps any saved messages.",
        );
    } finally {
      stream.current = null;
      setBusy(false);
      setStreamed("");
      if (live.current === current)
        await refresh(current).catch((error) => setNotice(error.message));
    }
  }
  async function feedback(message: Message, vote: number) {
    if (!session) return;
    try {
      await decode(
        await fetch("/api/v1/widget/feedback", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ message_id: message.id, vote }),
        }),
      );
      setVotes((previous) => ({ ...previous, [message.id]: vote }));
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "Could not save feedback.",
      );
    }
  }
  return (
    <main
      className={`resolve-app customer-app ${embedded ? "customer-embedded" : ""}`}
    >
      <section className="customer-shell" aria-label="Customer support">
        <header className="customer-header">
          <span className="customer-brand-mark" aria-hidden="true">
            ✦
          </span>
          <div>
            <span className="r-eyebrow">RESOLVE AI · CUSTOMER SUPPORT</span>
            <h1>{session?.company_name ?? "Your support space"}</h1>
          </div>
          <div className="customer-identity">
            <span
              className={`r-role-status ${session?.verified ? "r-role-customer" : ""}`}
            >
              <i />
              {session
                ? session.verified
                  ? "Customer · Signed in"
                  : "Guest customer"
                : signedOut
                  ? "Signed out"
                  : "Connecting"}
            </span>
            {session && (
              <button
                className="r-button r-logout"
                onClick={() => void logout()}
                disabled={busy}
              >
                Log out
              </button>
            )}
          </div>
        </header>
        {notice && (
          <div className="customer-notice" role="alert">
            {notice}
            <button aria-label="Dismiss message" onClick={() => setNotice("")}>
              ×
            </button>
          </div>
        )}
        {opening ? (
          <div className="customer-welcome">
            <span className="customer-spark">✦</span>
            <h2>Opening your conversation…</h2>
            <p>Company answers. A real person when you need one.</p>
          </div>
        ) : !session ? (
          <div className="customer-welcome">
            <span className="customer-spark">✦</span>
            <h2>
              {signedOut
                ? "You’re safely signed out."
                : "Let’s get you connected."}
            </h2>
            <p>
              Use the private access link provided by the company for order
              support, or start a guest chat for company policies.
            </p>
            <form
              className="r-stacked-form"
              onSubmit={(event) => {
                event.preventDefault();
                void open(access);
              }}
            >
              <label>
                Private customer access code
                <input
                  autoComplete="off"
                  type="password"
                  value={access}
                  onChange={(event) => setAccess(event.target.value)}
                  placeholder="Paste the code from your private link"
                />
              </label>
              <button className="r-button r-primary" disabled={!access}>
                Sign in as customer
              </button>
            </form>
            <button className="r-button" onClick={() => void open()}>
              Start guest chat
            </button>
          </div>
        ) : (
          <>
            <div className="customer-context">
              <span className="r-online-dot" />
              {session.verified
                ? `Welcome, ${session.name}. Your order access is private.`
                : "Ask about company policies. A private access link is needed for order details."}
            </div>
            <div
              className="customer-messages"
              aria-live="polite"
              aria-relevant="additions text"
              aria-label="Conversation messages"
            >
              {!conversation.messages.length && (
                <div className="customer-welcome">
                  <span className="customer-spark">✦</span>
                  <span className="r-eyebrow">
                    A LITTLE HELP, RIGHT WHEN YOU NEED IT
                  </span>
                  <h2>How can we help today?</h2>
                  <p>
                    Ask a question, check an order, or reach your support team.
                    Every answer starts with your company’s information.
                  </p>
                  <div className="customer-suggestions">
                    {[
                      "What is your shipping policy?",
                      "I need help with an order",
                      "I’d like to speak to a human",
                    ].map((value) => (
                      <button
                        key={value}
                        onClick={() => void send(undefined, value)}
                        disabled={busy}
                      >
                        {value}
                        <span>↗</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {conversation.messages.map((message) => (
                <article
                  className={`customer-message customer-${message.role}`}
                  key={message.id}
                >
                  <span className="customer-message-author">
                    {message.role === "customer"
                      ? "You"
                      : message.role === "agent"
                        ? "Support agent · Reviewed reply"
                        : "ResolveAI"}
                  </span>
                  <div className="customer-message-bubble">
                    {message.content}
                  </div>
                  {!!message.sources.length && (
                    <div className="r-sources">
                      {message.sources.map((source) => (
                        <details
                          key={`${source.document_id}-${source.position}`}
                        >
                          <summary>
                            [{source.id}] {source.filename}
                          </summary>
                          <blockquote>{source.text}</blockquote>
                        </details>
                      ))}
                    </div>
                  )}
                  {["assistant", "agent"].includes(message.role) && (
                    <div className="customer-feedback">
                      <span>Was this helpful?</span>
                      {[1, -1].map((vote) => (
                        <button
                          key={vote}
                          aria-pressed={votes[message.id] === vote}
                          onClick={() => void feedback(message, vote)}
                        >
                          {vote === 1 ? "Yes" : "No"}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              ))}
              {streamed && (
                <article className="customer-message customer-assistant">
                  <span className="customer-message-author">ResolveAI</span>
                  <div className="customer-message-bubble">{streamed}</div>
                </article>
              )}
              {(busy || conversation.processing) && !streamed && (
                <p className="customer-thinking" role="status">
                  <span />
                  <span />
                  <span />
                  Checking your company information…
                </p>
              )}
              {conversation.status === "waiting_for_agent" && (
                <div className="customer-handoff" role="status">
                  <span>◷</span>
                  <div>
                    <strong>Your support team is on it.</strong>
                    <p>
                      A human reviews the reply before it reaches you here. You
                      can leave this page open or return using your private
                      access link.
                    </p>
                  </div>
                </div>
              )}
              <div ref={bottom} />
            </div>
            <form
              className="customer-composer"
              onSubmit={(event) => void send(event)}
            >
              <label className="customer-message-label">
                Your message
                <textarea
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  maxLength={4000}
                  rows={2}
                  placeholder="Ask a question or share your order reference…"
                  disabled={busy || conversation.processing}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                />
              </label>
              <button
                className="r-button r-primary"
                disabled={busy || conversation.processing || !text.trim()}
                aria-label="Send message"
              >
                Send <span aria-hidden="true">↗</span>
              </button>
            </form>
            <footer className="customer-footer">
              <span>✦ Evidence first. Human approved.</span>
              <span>Customer session · 1 hour</span>
            </footer>
          </>
        )}
      </section>
      {!embedded && (
        <Link className="customer-staff-link" href="/login">
          Staff sign in →
        </Link>
      )}
    </main>
  );
}
