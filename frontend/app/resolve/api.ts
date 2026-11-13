import type { AuthSession, User } from "./types";

export class APIError extends Error {
  constructor(
    public status: number,
    message: string,
    public requestId?: string,
  ) {
    super(message);
  }
}

let accessToken = "";
let profile: User | null = null;
let pendingRefresh: Promise<AuthSession> | null = null;
let revision = 0;
let latestSession: AuthSession | null = null;
const sessionListeners = new Set<() => void>();

export function subscribeSession(listener: () => void) {
  sessionListeners.add(listener);
  return () => {
    sessionListeners.delete(listener);
  };
}
function publishSession() {
  for (const listener of sessionListeners) listener();
}

export function acceptSession(session: AuthSession) {
  revision++;
  latestSession = session;
  accessToken = session.access_token;
  profile = session.user;
  publishSession();
}

export function clearSession() {
  revision++;
  latestSession = null;
  accessToken = "";
  profile = null;
  publishSession();
}

export function currentUser() {
  return profile;
}

async function authLock<T>(operation: () => Promise<T>): Promise<T> {
  if (typeof navigator !== "undefined" && navigator.locks)
    return await navigator.locks.request("resolveai-auth-session", operation);
  return await operation();
}

async function readResponse<T>(response: Response): Promise<T> {
  const data =
    response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.error?.message ?? data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((item: { msg?: string }) => item.msg ?? "Invalid value")
              .join("; ")
          : `Request failed (${response.status}).`;
    throw new APIError(response.status, message, data?.error?.request_id);
  }
  return data as T;
}

async function send(path: string, options: RequestInit) {
  try {
    return await fetch(path, {
      ...options,
      credentials: "include",
      cache: "no-store",
    });
  } catch {
    throw new Error(
      "Cannot reach ResolveAI. Check that Docker services are running, then try again.",
    );
  }
}

export async function csrfToken(): Promise<string> {
  if (typeof document !== "undefined") {
    const cookie = document.cookie
      .split("; ")
      .find((part) => part.startsWith("resolveai_csrf="));
    if (cookie) return cookie.slice("resolveai_csrf=".length);
  }
  return (
    await readResponse<{ csrf_token: string }>(
      await send("/api/v1/auth/csrf", {}),
    )
  ).csrf_token;
}

export async function refreshSession(): Promise<AuthSession> {
  if (!pendingRefresh) {
    const startedAt = revision;
    const rotate = async () => {
      if (startedAt !== revision) {
        if (latestSession) return latestSession;
        throw new APIError(401, "Your session changed. Sign in again.");
      }
      const session = await readResponse<AuthSession>(
        await send("/api/v1/auth/refresh", {
          method: "POST",
          headers: { "X-CSRF-Token": await csrfToken() },
        }),
      );
      acceptSession(session);
      return session;
    };
    // Serialise rotation across browser tabs. Tokens never enter localStorage.
    pendingRefresh = authLock(rotate)
      .catch((error) => {
        if (startedAt !== revision && latestSession) return latestSession;
        if (
          startedAt === revision &&
          error instanceof APIError &&
          error.status === 401
        )
          clearSession();
        throw error;
      })
      .finally(() => {
        pendingRefresh = null;
      });
  }
  return pendingRefresh;
}

export async function restoreSession(): Promise<User> {
  if (!accessToken) return (await refreshSession()).user;
  const user = await request<User>("/api/v1/auth/me");
  profile = user;
  if (latestSession) latestSession = { ...latestSession, user };
  publishSession();
  return user;
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
  csrf = "",
): Promise<T> {
  if (!path.startsWith("/api/"))
    throw new Error("ResolveAI requests must use its same-origin API.");
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (
    options.method === "POST" &&
    [
      "/api/v1/auth/login",
      "/api/v1/auth/signup",
      "/api/v1/auth/logout",
    ].includes(path)
  ) {
    return await authLock(async () => {
      headers.set("X-CSRF-Token", await csrfToken());
      const result = await readResponse<T>(
        await send(path, { ...options, headers }),
      );
      if (path.endsWith("/logout")) clearSession();
      else acceptSession(result as AuthSession);
      return result;
    });
  }
  if (options.method && options.method !== "GET")
    headers.set("X-CSRF-Token", csrf || (await csrfToken()));
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const originalWorkspace = profile?.active_workspace_id;
  const originalUser = profile?.id;
  if (originalWorkspace && !headers.has("X-Workspace-ID"))
    headers.set("X-Workspace-ID", originalWorkspace);
  let response = await send(path, { ...options, headers });
  const refreshable =
    path === "/api/v1/auth/me" ||
    (!path.startsWith("/api/v1/auth/") && !path.startsWith("/api/v1/widget/"));
  if (response.status === 401 && refreshable) {
    try {
      const session = await refreshSession();
      if (
        (originalUser && session.user.id !== originalUser) ||
        (originalWorkspace &&
          session.user.active_workspace_id !== originalWorkspace)
      ) {
        window.dispatchEvent(new Event("resolveai-workspace-changed"));
        throw new APIError(
          409,
          "Your workspace access changed. Select the intended workspace before retrying.",
        );
      }
      headers.set("Authorization", `Bearer ${session.access_token}`);
      response = await send(path, { ...options, headers });
    } catch (error) {
      if (
        typeof window !== "undefined" &&
        error instanceof APIError &&
        error.status === 401
      )
        window.dispatchEvent(new Event("resolveai-session-ended"));
      throw error;
    }
  }
  return readResponse<T>(response);
}

export async function signOut() {
  await request<void>("/api/v1/auth/logout", { method: "POST" });
  clearSession();
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel("resolveai-auth");
    channel.postMessage("logout");
    channel.close();
  }
}
