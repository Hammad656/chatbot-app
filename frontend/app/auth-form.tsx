"use client";

import { FormEvent, useEffect, useState, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import LiquidOrb from "./liquid-orb";
import {
  acceptSession,
  APIError,
  request,
  restoreSession,
} from "./resolve/api";
import type { AuthSession, User } from "./resolve/types";
import "./resolve/support.css";

export default function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const destination = (user: User) =>
    sessionStorage.getItem("resolveai-invitation")
      ? "/invite"
      : user.active_workspace_id
        ? "/knowledge"
        : "/onboarding";
  useEffect(() => {
    let active = true;
    const element = formRef.current;
    void restoreSession()
      .then((user) => {
        if (active)
          router.replace(
            sessionStorage.getItem("resolveai-invitation")
              ? "/invite"
              : user.active_workspace_id
                ? "/knowledge"
                : "/onboarding",
          );
      })
      .catch((problem) => {
        if (active && !(problem instanceof APIError && problem.status === 401))
          setError(
            problem instanceof Error
              ? problem.message
              : "Cannot connect to ResolveAI.",
          );
      });
    return () => {
      active = false;
      element?.reset();
    };
  }, [router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const element = event.currentTarget;
    try {
      const session = await request<AuthSession>(`/api/v1/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
          ...(mode === "signup" ? { full_name: form.get("full_name") } : {}),
        }),
      });
      acceptSession(session);
      element.reset();
      setShowPassword(false);
      router.replace(destination(session.user));
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Sign in failed. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="resolve-app">
      <div className="r-auth-shell">
        <div className="r-auth-story">
          <Link className="r-logo" href="/login">
            <strong>
              resolve<span>AI</span>
            </strong>
          </Link>
          <LiquidOrb />
          <span className="r-eyebrow">THOUGHTFUL SUPPORT. HUMAN CONTROL.</span>
          <h1>
            A clearer path
            <br />
            <span>to resolution.</span>
          </h1>
          <p>
            Your company knowledge. Your team.
            <br />
            One workspace for thoughtful customer support.
          </p>
        </div>
        <section className="r-auth-card" aria-labelledby="auth-title">
          <span className="r-eyebrow">YOUR RESOLVEAI ACCOUNT</span>
          <h1 id="auth-title">
            {mode === "login" ? "Welcome back" : "Create your account"}
          </h1>
          <p>
            {mode === "login"
              ? "Sign in to your company workspace."
              : "Set up your account, then create or join a workspace."}
          </p>
          {error && (
            <div className="r-inline-error" role="alert">
              {error}
            </div>
          )}
          <noscript>Enable JavaScript to sign in securely.</noscript>
          <form ref={formRef} method="post" onSubmit={submit}>
            {mode === "signup" && (
              <label>
                Full name
                <input
                  name="full_name"
                  autoComplete="name"
                  maxLength={100}
                  required
                  placeholder="Your name"
                />
              </label>
            )}
            <label>
              Email address
              <input
                name="email"
                type="email"
                autoComplete="email"
                required
                maxLength={320}
                placeholder="you@company.com"
              />
            </label>
            <label>
              Password
              <div className="r-password-field">
                <input
                  name="password"
                  aria-label="Password"
                  type={showPassword ? "text" : "password"}
                  autoComplete={
                    mode === "login" ? "current-password" : "new-password"
                  }
                  required
                  minLength={mode === "signup" ? 12 : 1}
                  maxLength={128}
                  placeholder={
                    mode === "signup"
                      ? "At least 12 characters"
                      : "Your password"
                  }
                />
                <button
                  type="button"
                  className="r-text-button"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </label>
            <button className="r-button r-primary" disabled={busy}>
              {busy
                ? "One moment…"
                : mode === "login"
                  ? "Sign in"
                  : "Create account"}
            </button>
          </form>
          <p className="r-auth-switch">
            {mode === "login"
              ? "New to ResolveAI?"
              : "Already have an account?"}{" "}
            <Link href={mode === "login" ? "/signup" : "/login"}>
              {mode === "login" ? "Create an account" : "Sign in"}
            </Link>
          </p>
        </section>
      </div>
    </main>
  );
}
