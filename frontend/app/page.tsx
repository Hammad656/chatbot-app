"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";

type User = {
  id: string;
  email: string;
  created_at: string;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
  error?: boolean;
};

type Chat = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: Message[];
};

type CVStatus = {
  uploaded: boolean;
  filename: string | null;
  chunk_count: number;
  cleanup_pending: boolean;
};

type AuthState = "loading" | "signed-out" | "signed-in";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const suggestions = [
  {
    icon: "✳",
    title: "Summarize my experience",
    description: "Get an overview from your CV",
    prompt: "Summarize my professional experience using only my CV.",
  },
  {
    icon: "↗",
    title: "Find my listed skills",
    description: "See the skills mentioned in your CV",
    prompt: "Which skills are listed in my CV?",
  },
  {
    icon: "⌘",
    title: "Review my work history",
    description: "Explore roles and responsibilities",
    prompt: "What roles and responsibilities are described in my CV?",
  },
  {
    icon: "✦",
    title: "Check my education",
    description: "Review the education in your CV",
    prompt: "What education or qualifications are listed in my CV?",
  },
];

function Icon({
  name,
  size = 18,
}: {
  name: "plus" | "chat" | "spark" | "send" | "menu" | "close" | "copy" | "logout";
  size?: number;
}) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    chat: (
      <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" />
    ),
    spark: (
      <>
        <path d="m12 3 1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2L12 3Z" />
        <path d="m19 14 1.2 2.8L23 18l-2.8 1.2L19 22l-1.2-2.8L15 18l2.8-1.2L19 14Z" />
      </>
    ),
    send: (
      <>
        <path d="m22 2-7 20-4-9-9-4Z" />
        <path d="M22 2 11 13" />
      </>
    ),
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    close: <path d="m18 6-12 12M6 6l12 12" />,
    copy: (
      <>
        <rect x="8" y="8" width="13" height="13" rx="2" />
        <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
      </>
    ),
    logout: (
      <>
        <path d="M10 17l5-5-5-5M15 12H3" />
        <path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6" />
      </>
    ),
  };

  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

async function readError(response: Response) {
  const result: unknown = await response.json().catch(() => null);
  if (typeof result === "object" && result !== null) {
    const detail = (result as Record<string, unknown>).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" &&
          item !== null &&
          typeof (item as Record<string, unknown>).msg === "string"
            ? String((item as Record<string, unknown>).msg)
            : "",
        )
        .filter(Boolean)
        .join(" ");
    }
  }
  return "Something went wrong. Please try again.";
}

function createId() {
  return window.crypto.randomUUID();
}

export default function Home() {
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [csrfToken, setCsrfToken] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [chats, setChats] = useState<Chat[]>([]);
  const [cv, setCV] = useState<CVStatus | null>(null);
  const [cvBusy, setCVBusy] = useState(false);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pendingChatIds, setPendingChatIds] = useState<string[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const cvFileInputRef = useRef<HTMLInputElement>(null);

  async function getCsrfToken() {
    const response = await fetch(`${API_URL}/api/auth/csrf`, {
      credentials: "include",
    });
    if (!response.ok) throw new Error(await readError(response));
    const result: unknown = await response.json();
    if (
      typeof result !== "object" ||
      result === null ||
      typeof (result as Record<string, unknown>).csrf_token !== "string"
    ) {
      throw new Error("The server did not provide a security token.");
    }
    return String((result as Record<string, unknown>).csrf_token);
  }

  async function loadChats() {
    const response = await fetch(`${API_URL}/api/chats`, {
      credentials: "include",
    });
    if (!response.ok) throw new Error(await readError(response));
    const result: unknown = await response.json();
    if (!Array.isArray(result)) {
      throw new Error("The server returned an invalid conversation list.");
    }
    const savedChats = result as Chat[];
    setChats(savedChats);
    setActiveChatId((current) =>
      savedChats.some((chat) => chat.id === current)
        ? current
        : (savedChats[0]?.id ?? null),
    );
    return savedChats;
  }

  async function loadCV() {
    const response = await fetch(`${API_URL}/api/cv`, {
      credentials: "include",
    });
    if (!response.ok) throw new Error(await readError(response));
    const result = (await response.json()) as CVStatus;
    setCV(result);
    return result;
  }

  useEffect(() => {
    let active = true;

    async function restoreSession() {
      try {
        const token = await getCsrfToken();
        if (!active) return;
        setCsrfToken(token);

        const response = await fetch(`${API_URL}/api/auth/me`, {
          credentials: "include",
        });
        if (response.status === 401) {
          if (active) setAuthState("signed-out");
          return;
        }
        if (!response.ok) throw new Error(await readError(response));

        const signedInUser = (await response.json()) as User;
        if (!active) return;
        setUser(signedInUser);
        await loadChats();
        await loadCV();
        if (active) setAuthState("signed-in");
      } catch (error) {
        console.error("Could not restore the session.", error);
        if (active) {
          setNotice(
            error instanceof TypeError
              ? "Could not connect to the server. Check that the backend is running."
              : error instanceof Error
                ? error.message
                : "Could not connect to the server.",
          );
          setAuthState("signed-out");
        }
      }
    }

    void restoreSession();
    return () => {
      active = false;
    };
  }, []);

  const activeChat = chats.find((chat) => chat.id === activeChatId);
  const isSending = activeChat ? pendingChatIds.includes(activeChat.id) : false;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activeChat?.messages.length, activeChatId, isSending]);

  async function createChat() {
    if (!csrfToken) {
      setNotice("The security token is not ready yet. Refresh and try again.");
      return;
    }
    const response = await fetch(`${API_URL}/api/chats`, {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": csrfToken },
    });
    if (!response.ok) throw new Error(await readError(response));
    const chat = (await response.json()) as Chat;
    setChats((current) => [chat, ...current]);
    setActiveChatId(chat.id);
    setInput("");
    setSidebarOpen(false);
    textareaRef.current?.focus();
  }

  async function startNewChat() {
    try {
      await createChat();
    } catch (error) {
      console.error("Could not start a conversation.", error);
      setNotice(
        error instanceof TypeError
          ? "Could not connect to the server."
          : error instanceof Error
            ? error.message
            : "Could not start a conversation.",
      );
    }
  }

  async function sendMessage(messageText = input) {
    const content = messageText.trim();
    if (!content || !activeChat || pendingChatIds.includes(activeChat.id)) return;
    if (!csrfToken) {
      setNotice("The security token is not ready yet. Refresh and try again.");
      return;
    }

    const chatId = activeChat.id;
    const userMessage: Message = {
      id: createId(),
      role: "user",
      content,
    };
    setChats((current) =>
      current.map((chat) =>
        chat.id === chatId
          ? {
              ...chat,
              title:
                chat.messages.length === 0
                  ? content.slice(0, 77) + (content.length > 80 ? "..." : "")
                  : chat.title,
              messages: [...chat.messages, userMessage],
            }
          : chat,
      ),
    );
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setPendingChatIds((current) => [...current, chatId]);
    setNotice("");

    try {
      const response = await fetch(`${API_URL}/api/chat/${chatId}`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ conversation_id: chatId, content }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as {
        answer: string;
        message: Message;
      };
      setChats((current) =>
        current.map((chat) =>
          chat.id === chatId
            ? { ...chat, messages: [...chat.messages, result.message] }
            : chat,
        ),
      );
    } catch (error) {
      console.error("Could not send message.", error);
      const errorMessage: Message = {
        id: createId(),
        role: "assistant",
        error: true,
        content:
          error instanceof TypeError
            ? "I couldn't connect to the assistant. Make sure the FastAPI backend is running, then try again."
            : error instanceof Error
              ? error.message
              : "Something went wrong. Please try again.",
      };
      setChats((current) =>
        current.map((chat) =>
          chat.id === chatId
            ? { ...chat, messages: [...chat.messages, errorMessage] }
            : chat,
        ),
      );
    } finally {
      setPendingChatIds((current) => current.filter((id) => id !== chatId));
    }
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAuthBusy(true);
    setNotice("");
    try {
      const response = await fetch(`${API_URL}/api/auth/${authMode}`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) throw new Error(await readError(response));

      const signedInUser = (await response.json()) as User;
      setUser(signedInUser);
      setPassword("");
      setAuthState("signed-in");
      const savedChats = await loadChats();
      await loadCV();
      if (savedChats.length === 0) await createChat();
    } catch (error) {
      console.error(`Could not ${authMode}.`, error);
      setNotice(
        error instanceof TypeError
          ? "Could not connect to the server. Check that the backend is running."
          : error instanceof Error
            ? error.message
            : "Authentication failed. Please try again.",
      );
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut() {
    try {
      const response = await fetch(`${API_URL}/api/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": csrfToken },
      });
      if (!response.ok) throw new Error(await readError(response));
      setUser(null);
      setChats([]);
      setCV(null);
      setActiveChatId(null);
      setAuthState("signed-out");
      setCsrfToken(await getCsrfToken());
    } catch (error) {
      console.error("Could not sign out.", error);
      setNotice(
        error instanceof TypeError
          ? "Could not connect to the server."
          : error instanceof Error
            ? error.message
            : "Could not sign out.",
      );
    }
  }

  async function copyMessage(message: Message) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.id);
      window.setTimeout(() => setCopiedMessageId(null), 1600);
    } catch (error) {
      console.error("Could not copy the assistant response.", error);
      setNotice("Could not copy that response to your clipboard.");
    }
  }

  async function uploadCV(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    const inputElement = event.currentTarget;
    setCVBusy(true);
    setNotice("");

    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(`${API_URL}/api/cv`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": csrfToken },
        body: formData,
      });
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as CVStatus;
      setCV(result);
      setNotice(
        result.cleanup_pending
          ? "Your CV is indexed, but an older copy could not be deleted. Remove the CV to clear all its indexed data."
          : `Your CV is indexed for chat: ${result.filename ?? file.name}.`,
      );
    } catch (error) {
      console.error("Could not index the CV.", error);
      setNotice(
        error instanceof TypeError
          ? "Could not connect to the server."
          : error instanceof Error
            ? error.message
            : "Could not index the CV.",
      );
    } finally {
      inputElement.value = "";
      setCVBusy(false);
    }
  }

  async function removeCV() {
    if (!window.confirm("Remove your CV and its indexed content?")) return;
    setCVBusy(true);
    setNotice("");
    try {
      const response = await fetch(`${API_URL}/api/cv`, {
        method: "DELETE",
        credentials: "include",
        headers: { "X-CSRF-Token": csrfToken },
      });
      if (!response.ok) throw new Error(await readError(response));
      setCV({
        uploaded: false,
        filename: null,
        chunk_count: 0,
        cleanup_pending: false,
      });
      setNotice("Your CV and its indexed content were removed.");
    } catch (error) {
      console.error("Could not remove the CV.", error);
      setNotice(
        error instanceof TypeError
          ? "Could not connect to the server."
          : error instanceof Error
            ? error.message
            : "Could not remove the CV.",
      );
    } finally {
      setCVBusy(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendMessage();
  }

  return (
    <main className="workspace">
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="Close conversation menu"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar${sidebarOpen ? " sidebar-open" : ""}`}>
        <div className="brand" aria-label="Morrow">
          <span className="brand-mark"><Icon name="spark" size={19} /></span>
          <span className="brand-name">morrow<span>.</span></span>
        </div>

        {authState === "signed-in" && (
          <>
            <button className="new-chat-button" onClick={() => void startNewChat()}>
              <Icon name="plus" size={17} />
              <span>New conversation</span>
            </button>

            <div className="sidebar-section">
              <div className="section-label">YOUR SPACE</div>
              <button
                className="sidebar-link sidebar-link-active"
                onClick={() => void startNewChat()}
              >
                <Icon name="chat" size={17} />
                <span>All conversations</span>
              </button>
            </div>

            <div className="conversation-section">
              <div className="section-heading">
                <span className="section-label">RECENT</span>
                {chats.length > 0 && (
                  <span className="conversation-count">{chats.length}</span>
                )}
              </div>
              <div className="conversation-list">
                {chats.map((chat) => (
                  <button
                    key={chat.id}
                    className={`conversation-item${chat.id === activeChatId ? " conversation-active" : ""}`}
                    onClick={() => {
                      setActiveChatId(chat.id);
                      setSidebarOpen(false);
                    }}
                    title={chat.title}
                  >
                    <Icon name="chat" size={15} />
                    <span>{chat.title}</span>
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        <div className="sidebar-footer">
          {authState === "signed-in" && (
            <section className="cv-manager" aria-label="CV knowledge">
              <div className="cv-manager-heading">
                <span className="section-label">CV KNOWLEDGE</span>
                <span className={`cv-status-dot${cv?.uploaded ? " cv-status-ready" : ""}`} />
              </div>
              <p className="cv-manager-status">
                {cv === null
                  ? "Checking your CV..."
                  : cv.uploaded
                    ? `${cv.filename} · ${cv.chunk_count} passages${cv.cleanup_pending ? " · old data cleanup pending" : ""}`
                    : "Add a CV to ask questions about your experience."}
              </p>
              <div className="cv-manager-actions">
                <input
                  ref={cvFileInputRef}
                  className="cv-file-input"
                  type="file"
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  onChange={(event) => void uploadCV(event)}
                  disabled={cvBusy}
                  aria-label="Choose a CV file"
                />
                <button
                  className="cv-upload-button"
                  type="button"
                  onClick={() => cvFileInputRef.current?.click()}
                  disabled={cvBusy || cv === null}
                >
                  {cvBusy ? "Working..." : cv?.uploaded ? "Replace CV" : "Upload CV"}
                </button>
                {cv?.uploaded && (
                  <button
                    className="cv-remove-button"
                    type="button"
                    onClick={() => void removeCV()}
                    disabled={cvBusy}
                  >
                    Remove
                  </button>
                )}
              </div>
              <p className="cv-manager-hint">
                PDF or DOCX, up to 8 MB. CV passages go to Pinecone and relevant
                passages are shared with OpenAI when answering.
              </p>
            </section>
          )}
          <div className="privacy-card">
            <span className="privacy-icon"><Icon name="spark" size={16} /></span>
            <div>
              <strong>A little space to think</strong>
              <p>Your conversations are saved to your account.</p>
            </div>
          </div>
          <div className="sidebar-version">MADE FOR THE WHAT-IFS <span>✳</span></div>
        </div>
      </aside>

      <section className="chat-panel">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open conversation menu"
          >
            <Icon name="menu" size={19} />
          </button>
          <div className="assistant-label">
            <span className="online-dot" />
            <span>{authState === "signed-in" ? "CV-only assistant" : "Your thought partner"}</span>
            <span className="topbar-divider">/</span>
            <span className="topbar-subtitle">Here for whatever&apos;s on your mind</span>
          </div>
          {authState === "signed-in" && user && (
            <div className="account-controls">
              <span className="account-email" title={user.email}>{user.email}</span>
              <button className="sign-out-button" onClick={() => void signOut()}>
                <Icon name="logout" size={15} />
                <span>Sign out</span>
              </button>
            </div>
          )}
        </header>

        {notice && (
          <div className="notice-banner" role="status">
            <span>{notice}</span>
            <button onClick={() => setNotice("")} aria-label="Dismiss notice">
              <Icon name="close" size={15} />
            </button>
          </div>
        )}

        <div className="chat-scroll">
          <div className={`chat-content${activeChat?.messages.length ? " has-messages" : ""}`}>
            {authState === "loading" ? (
              <section className="welcome auth-loading" aria-label="Loading account">
                <div className="welcome-mark"><Icon name="spark" size={25} /></div>
                <p className="welcome-copy">Getting your space ready...</p>
              </section>
            ) : authState === "signed-out" ? (
              <section className="auth-card">
                <div className="welcome-mark"><Icon name="spark" size={25} /></div>
                <div className="welcome-eyebrow"><span /> YOUR THOUGHT PARTNER</div>
                <h1>{authMode === "login" ? "Welcome back." : "Make room for ideas."}</h1>
                <p className="auth-copy">
                  {authMode === "login"
                    ? "Sign in to pick up where you left off."
                    : "Create an account to keep your conversations safe and in sync."}
                </p>
                <form className="auth-form" onSubmit={submitAuth}>
                  <label htmlFor="email">Email address</label>
                  <input
                    id="email"
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                    maxLength={320}
                  />
                  <label htmlFor="password">Password</label>
                  <input
                    id="password"
                    type="password"
                    autoComplete={authMode === "login" ? "current-password" : "new-password"}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                    minLength={12}
                    maxLength={128}
                  />
                  {authMode === "signup" && (
                    <span className="password-hint">Use at least 12 characters.</span>
                  )}
                  <button
                    className="auth-submit"
                    type="submit"
                    disabled={authBusy || !csrfToken}
                  >
                    {authBusy
                      ? "One moment..."
                      : authMode === "login"
                        ? "Sign in"
                        : "Create account"}
                  </button>
                </form>
                <p className="auth-switch">
                  {authMode === "login" ? "New to Morrow?" : "Already have an account?"}
                  <button
                    onClick={() => {
                      setAuthMode(authMode === "login" ? "signup" : "login");
                      setNotice("");
                    }}
                  >
                    {authMode === "login" ? "Create an account" : "Sign in"}
                  </button>
                </p>
                <p className="auth-security">
                  Your password is securely hashed and never stored as plain text.
                </p>
              </section>
            ) : !activeChat?.messages.length ? (
              <section className="welcome">
                <div className="welcome-mark">
                  <Icon name="spark" size={25} />
                  <span className="welcome-orbit orbit-one" />
                  <span className="welcome-orbit orbit-two" />
                </div>
                <div className="welcome-eyebrow"><span /> YOUR THOUGHT PARTNER</div>
                <h1>A good place to<br /><span>start somewhere.</span></h1>
                <p className="welcome-copy">
                  Ask about your experience, skills, roles, or education. Answers
                  are based only on the CV you upload.
                </p>
                <div className="suggestions">
                  {suggestions.map((suggestion) => (
                    <button
                      className="suggestion-card"
                      key={suggestion.title}
                      onClick={() => void sendMessage(suggestion.prompt)}
                      disabled={isSending}
                    >
                      <span className="suggestion-icon">{suggestion.icon}</span>
                      <span className="suggestion-text">
                        <strong>{suggestion.title}</strong>
                        <span>{suggestion.description}</span>
                      </span>
                      <span className="suggestion-arrow">↗</span>
                    </button>
                  ))}
                </div>
                <div className="welcome-footnote">
                  <span className="footnote-line" />
                  <span>CURIOUS BY NATURE. HELPFUL BY DESIGN.</span>
                  <span className="footnote-line" />
                </div>
              </section>
            ) : (
              <section className="messages" aria-live="polite">
                <div className="conversation-intro">
                  <span className="conversation-intro-icon"><Icon name="spark" size={17} /></span>
                  <span>A fresh thread. Take it wherever you like.</span>
                </div>
                {activeChat.messages.map((message) => (
                  <article
                    className={`message message-${message.role}${message.error ? " message-error" : ""}`}
                    key={message.id}
                  >
                    {message.role === "assistant" && (
                      <div className="message-avatar"><Icon name="spark" size={17} /></div>
                    )}
                    <div className="message-body">
                      <div className="message-meta">
                        <span>{message.role === "user" ? "You" : "Morrow"}</span>
                        {message.role === "assistant" && !message.error && (
                          <button
                            className="copy-button"
                            onClick={() => void copyMessage(message)}
                            aria-label={copiedMessageId === message.id ? "Copied" : "Copy response"}
                          >
                            {copiedMessageId === message.id
                              ? <span>Copied</span>
                              : <Icon name="copy" size={14} />}
                          </button>
                        )}
                      </div>
                      <p>{message.content}</p>
                    </div>
                  </article>
                ))}
                {isSending && (
                  <div className="message message-assistant typing-message">
                    <div className="message-avatar"><Icon name="spark" size={17} /></div>
                    <div className="typing-indicator" aria-label="Morrow is thinking">
                      <span /><span /><span />
                    </div>
                  </div>
                )}
                <div ref={bottomRef} />
              </section>
            )}
          </div>
        </div>

        {authState === "signed-in" && (
          <div className="composer-area">
            <form className="composer" onSubmit={handleSubmit}>
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(event) => {
                  setInput(event.target.value);
                  event.target.style.height = "auto";
                  event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`;
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                placeholder="Ask a question about your CV..."
                aria-label="Message the assistant"
                maxLength={12000}
                rows={1}
                disabled={!activeChat || isSending}
              />
              <div className="composer-bottom">
                <span className="composer-hint">
                  <span className="sparkle-tiny">✳</span>
                  Answers grounded only in your CV
                </span>
                <div className="composer-actions">
                  <span className="keyboard-hint">↵ <span>to send</span></span>
                  <button
                    className="send-button"
                    type="submit"
                    aria-label="Send message"
                    disabled={!input.trim() || !activeChat || isSending}
                  >
                    <Icon name="send" size={17} />
                  </button>
                </div>
              </div>
            </form>
            <p className="disclaimer">
              Morrow can get things wrong. For the important stuff, double-check.
            </p>
          </div>
        )}
      </section>
    </main>
  );
}
