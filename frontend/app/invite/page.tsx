"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  acceptSession,
  APIError,
  request,
  restoreSession,
  signOut,
} from "../resolve/api";
import type { AuthSession } from "../resolve/types";
import "../resolve/support.css";

export default function InvitationPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [signedIn, setSignedIn] = useState(false);
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const fragment = window.location.hash.slice(1);
    if (/^[A-Za-z0-9_-]{32,128}$/.test(fragment)) {
      sessionStorage.setItem("resolveai-invitation", fragment);
      window.history.replaceState(null, "", "/invite");
    }
    let active = true;
    const changed = () => {
      const fragment = window.location.hash.slice(1);
      if (/^[A-Za-z0-9_-]{32,128}$/.test(fragment)) {
        sessionStorage.setItem("resolveai-invitation", fragment);
        window.history.replaceState(null, "", "/invite");
        setToken(fragment);
      }
    };
    window.addEventListener("hashchange", changed);
    void restoreSession()
      .then((user) => {
        if (active) {
          setSignedIn(true);
          setEmail(user.email);
        }
      })
      .catch((problem) => {
        if (active && !(problem instanceof APIError && problem.status === 401))
          setError(problem.message);
      })
      .finally(() => {
        if (active)
          setToken(sessionStorage.getItem("resolveai-invitation") ?? "");
      });
    return () => {
      active = false;
      window.removeEventListener("hashchange", changed);
    };
  }, []);
  async function accept() {
    setBusy(true);
    setError("");
    try {
      const session = await request<AuthSession>("/api/v1/invitations/accept", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      acceptSession(session);
      sessionStorage.removeItem("resolveai-invitation");
      router.replace("/knowledge");
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Could not accept invitation.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="resolve-app">
      <div className="r-auth-shell">
        <section className="r-auth-card">
          <Link className="r-logo" href="/login">
            resolveAI
          </Link>
          <h1>Join your team</h1>
          <p>
            Invitations are valid for seven days and can be accepted only by the
            invited email address.
          </p>
          {error && (
            <p role="alert" className="r-inline-error">
              {error}
            </p>
          )}
          {!token ? (
            <p>
              Open the invitation link provided by your workspace owner or
              admin.
            </p>
          ) : signedIn ? (
            <>
              <p>
                Signed in as <strong>{email}</strong>
              </p>
              <button
                className="r-button r-primary"
                disabled={busy}
                onClick={() => void accept()}
              >
                {busy ? "Joining…" : "Accept invitation"}
              </button>
              <button
                className="r-text-button"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  void signOut()
                    .then(() => router.replace("/login"))
                    .catch((problem) => {
                      setError(problem.message);
                      setBusy(false);
                    });
                }}
              >
                Use another account
              </button>
            </>
          ) : (
            <>
              <p>Sign in or create an account using your invited email.</p>
              <Link className="r-button r-primary" href="/login">
                Sign in
              </Link>
              <Link className="r-button" href="/signup">
                Create account
              </Link>
            </>
          )}
          <Link
            className="r-text-button"
            href="/knowledge"
            onClick={() => sessionStorage.removeItem("resolveai-invitation")}
          >
            Back to workspace
          </Link>
        </section>
      </div>
    </main>
  );
}
