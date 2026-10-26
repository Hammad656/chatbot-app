"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  error?: boolean;
};

type Chat = {
  id: string;
  title: string;
  messages: Message[];
};

const STORAGE_KEY = "morrow-workspace-v1";
const STORAGE_EVENT = "morrow-workspace-change";
const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

type Workspace = {
  chats: Chat[];
  activeChatId: string | null;
  storageNotice?: string;
};

const EMPTY_WORKSPACE: Workspace = {
  chats: [{ id: "welcome", title: "New conversation", messages: [] }],
  activeChatId: "welcome",
};
const EMPTY_WORKSPACE_JSON = JSON.stringify(EMPTY_WORKSPACE);

const suggestions = [
  {
    icon: "✳",
    title: "Help me get organized",
    description: "Turn a busy week into a clear plan",
    prompt: "Help me organize my week and decide what to prioritize.",
  },
  {
    icon: "↗",
    title: "Write something great",
    description: "Draft, edit, or find the right words",
    prompt: "Help me write a clear, friendly email to reschedule a meeting.",
  },
  {
    icon: "⌘",
    title: "Make a tricky topic simple",
    description: "Get an explanation that actually clicks",
    prompt: "Explain how the internet works in simple terms.",
  },
  {
    icon: "✦",
    title: "Explore a new idea",
    description: "Brainstorm, research, and get inspired",
    prompt: "Help me brainstorm a few creative ideas for my next project.",
  },
];

function isSavedChat(value: unknown): value is Chat {
  if (typeof value !== "object" || value === null) return false;

  const chat = value as Record<string, unknown>;
  return (
    typeof chat.id === "string" &&
    typeof chat.title === "string" &&
    Array.isArray(chat.messages) &&
    chat.messages.every(
      (message: unknown) =>
        typeof message === "object" &&
        message !== null &&
        typeof (message as Record<string, unknown>).id === "string" &&
        ["user", "assistant"].includes(
          String((message as Record<string, unknown>).role),
        ) &&
        typeof (message as Record<string, unknown>).content === "string" &&
        ((message as Record<string, unknown>).error === undefined ||
          typeof (message as Record<string, unknown>).error === "boolean"),
    )
  );
}

function subscribeToWorkspace(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(STORAGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(STORAGE_EVENT, onChange);
  };
}

function getWorkspaceSnapshot() {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? EMPTY_WORKSPACE_JSON;
  } catch (error) {
    console.error("Could not read saved conversations.", error);
    return "__morrow_storage_unavailable__";
  }
}

function getServerWorkspaceSnapshot() {
  return EMPTY_WORKSPACE_JSON;
}

function parseWorkspace(snapshot: string): Workspace {
  if (snapshot === "__morrow_storage_unavailable__") {
    return {
      ...EMPTY_WORKSPACE,
      storageNotice: "Your browser could not access saved conversations.",
    };
  }

  try {
    const parsed: unknown = JSON.parse(snapshot);
    if (typeof parsed !== "object" || parsed === null) {
      throw new Error("Saved conversations have an invalid format.");
    }

    const saved = parsed as Record<string, unknown>;
    if (
      !Array.isArray(saved.chats) ||
      !saved.chats.every(isSavedChat) ||
      (saved.activeChatId !== null && typeof saved.activeChatId !== "string")
    ) {
      throw new Error("Saved conversations have an invalid format.");
    }

    const chats = saved.chats.filter(isSavedChat);
    const activeChatId =
      typeof saved.activeChatId === "string" &&
      chats.some((chat) => chat.id === saved.activeChatId)
        ? saved.activeChatId
        : (chats[0]?.id ?? null);

    return { chats, activeChatId };
  } catch (error) {
    console.error("Could not restore saved conversations.", error);
    return {
      ...EMPTY_WORKSPACE,
      storageNotice: "Your saved conversations could not be loaded.",
    };
  }
}

function Icon({
  name,
  size = 18,
}: {
  name: "plus" | "chat" | "spark" | "send" | "menu" | "close" | "copy";
  size?: number;
}) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    chat: (
      <>
        <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" />
      </>
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
    menu: (
      <>
        <path d="M4 6h16M4 12h16M4 18h16" />
      </>
    ),
    close: <path d="m18 6-12 12M6 6l12 12" />,
    copy: (
      <>
        <rect x="8" y="8" width="13" height="13" rx="2" />
        <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
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

function createId() {
  return window.crypto.randomUUID();
}

export default function Home() {
  const workspaceSnapshot = useSyncExternalStore(
    subscribeToWorkspace,
    getWorkspaceSnapshot,
    getServerWorkspaceSnapshot,
  );
  const workspace = useMemo(
    () => parseWorkspace(workspaceSnapshot),
    [workspaceSnapshot],
  );
  const { chats, activeChatId } = workspace;
  const [input, setInput] = useState("");
  const [pendingChatIds, setPendingChatIds] = useState<string[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  function saveWorkspace(nextChats: Chat[], nextActiveChatId: string | null) {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ chats: nextChats, activeChatId: nextActiveChatId }),
      );
      window.dispatchEvent(new Event(STORAGE_EVENT));
    } catch (error) {
      console.error("Could not save conversations.", error);
      setNotice("Your browser could not save this conversation.");
    }
  }

  const activeChat = chats.find((chat) => chat.id === activeChatId);
  const isSending = activeChat ? pendingChatIds.includes(activeChat.id) : false;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activeChat?.messages.length, activeChatId, isSending]);

  function updateChat(chatId: string, update: (chat: Chat) => Chat) {
    const latestWorkspace = parseWorkspace(getWorkspaceSnapshot());
    if (latestWorkspace.storageNotice) {
      setNotice(latestWorkspace.storageNotice);
      return;
    }

    saveWorkspace(
      latestWorkspace.chats.map((chat) =>
        chat.id === chatId ? update(chat) : chat,
      ),
      latestWorkspace.activeChatId,
    );
  }

  function startNewChat() {
    const newChat = { id: createId(), title: "New conversation", messages: [] };
    saveWorkspace([newChat, ...chats], newChat.id);
    setInput("");
    setSidebarOpen(false);
    textareaRef.current?.focus();
  }

  async function sendMessage(messageText = input) {
    const content = messageText.trim();
    if (!content || !activeChat || pendingChatIds.includes(activeChat.id)) return;

    const chatId = activeChat.id;
    const userMessage: Message = {
      id: createId(),
      role: "user",
      content,
    };
    const history = activeChat.messages
      .filter((message) => !message.error)
      .map(({ role, content: messageContent }) => ({
        role,
        content: messageContent,
      }));
    history.push({ role: "user", content });

    updateChat(chatId, (chat) => ({
      ...chat,
      title:
        chat.messages.length === 0
          ? content.slice(0, 38) + (content.length > 38 ? "…" : "")
          : chat.title,
      messages: [...chat.messages, userMessage],
    }));
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setPendingChatIds((current) => [...current, chatId]);
    setNotice("");

    try {
      const response = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      const result: unknown = await response.json();
      if (!response.ok) {
        const detail =
          typeof result === "object" &&
          result !== null &&
          typeof (result as Record<string, unknown>).detail === "string"
            ? String((result as Record<string, unknown>).detail)
            : "The assistant could not respond. Please try again.";
        throw new Error(detail);
      }
      if (
        typeof result !== "object" ||
        result === null ||
        typeof (result as Record<string, unknown>).answer !== "string"
      ) {
        throw new Error("The assistant returned an unexpected response.");
      }

      const assistantMessage: Message = {
        id: createId(),
        role: "assistant",
        content: String((result as Record<string, unknown>).answer),
      };
      updateChat(chatId, (chat) => ({
        ...chat,
        messages: [...chat.messages, assistantMessage],
      }));
    } catch (error) {
      const reason =
        error instanceof TypeError
          ? "I couldn't connect to the assistant. Make sure the FastAPI backend is running, then try again."
          : error instanceof Error
            ? error.message
            : "Something went wrong. Please try again.";
      const errorMessage: Message = {
        id: createId(),
        role: "assistant",
        content: reason,
        error: true,
      };
      updateChat(chatId, (chat) => ({
        ...chat,
        messages: [...chat.messages, errorMessage],
      }));
    } finally {
      setPendingChatIds((current) => current.filter((id) => id !== chatId));
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

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
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
          <span className="brand-mark">
            <Icon name="spark" size={19} />
          </span>
          <span className="brand-name">morrow<span>.</span></span>
        </div>

        <button className="new-chat-button" onClick={startNewChat}>
          <Icon name="plus" size={17} />
          <span>New conversation</span>
          <span className="shortcut">⌘ K</span>
        </button>

        <div className="sidebar-section">
          <div className="section-label">YOUR SPACE</div>
          <button
            className="sidebar-link sidebar-link-active"
            onClick={startNewChat}
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
                  saveWorkspace(chats, chat.id);
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

        <div className="sidebar-footer">
          <div className="privacy-card">
            <span className="privacy-icon"><Icon name="spark" size={16} /></span>
            <div>
              <strong>A little space to think</strong>
              <p>Your conversations stay in this browser.</p>
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
            <span>General assistant</span>
            <span className="topbar-divider">/</span>
            <span className="topbar-subtitle">Here for whatever&apos;s on your mind</span>
          </div>
          <button
            className="topbar-new"
            onClick={startNewChat}
            aria-label="Start a new conversation"
          >
            <Icon name="plus" size={17} />
            <span>New chat</span>
          </button>
        </header>

        {(notice || workspace.storageNotice) && (
          <div className="notice-banner" role="status">
            <span>{notice || workspace.storageNotice}</span>
            <button onClick={() => setNotice("")} aria-label="Dismiss notice">
              <Icon name="close" size={15} />
            </button>
          </div>
        )}

        <div className="chat-scroll">
          <div className={`chat-content${activeChat?.messages.length ? " has-messages" : ""}`}>
            {!activeChat?.messages.length ? (
              <section className="welcome">
                <div className="welcome-mark">
                  <Icon name="spark" size={25} />
                  <span className="welcome-orbit orbit-one" />
                  <span className="welcome-orbit orbit-two" />
                </div>
                <div className="welcome-eyebrow"><span /> YOUR THOUGHT PARTNER</div>
                <h1>A good place to<br /><span>start somewhere.</span></h1>
                <p className="welcome-copy">
                  Big question, small task, half-formed idea — bring it all.
                  We&apos;ll figure out the next step together.
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
                            {copiedMessageId === message.id ? (
                              <span>Copied</span>
                            ) : (
                              <Icon name="copy" size={14} />
                            )}
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
              placeholder="Ask anything, or just start thinking out loud..."
              aria-label="Message the assistant"
              maxLength={12000}
              rows={1}
              disabled={!activeChat || isSending}
            />
            <div className="composer-bottom">
              <span className="composer-hint">
                <span className="sparkle-tiny">✳</span>
                Thoughtful answers, one question at a time
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
      </section>
    </main>
  );
}
