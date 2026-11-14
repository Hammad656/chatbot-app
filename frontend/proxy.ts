import { NextRequest, NextResponse } from "next/server";

// Framing is restricted by the company's exact allowed-origin list. Public widget
// keys identify a company; private customer links provide order authorization.
export async function proxy(request: NextRequest) {
  const key = request.nextUrl.pathname.split("/")[2];
  const ancestors = ["'self'"];
  if (key && /^[a-zA-Z0-9_-]{16,64}$/.test(key)) {
    try {
      const api = (
        process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000"
      ).replace(/\/$/, "");
      const response = await fetch(`${api}/api/v1/widget/config/${key}`, {
        cache: "no-store",
        signal: AbortSignal.timeout(3000),
      });
      if (response.ok) {
        const data = await response.json();
        for (const origin of data.allowed_origins ?? []) {
          const parsed = new URL(origin);
          if (
            ["http:", "https:"].includes(parsed.protocol) &&
            parsed.origin === origin
          )
            ancestors.push(origin);
        }
      }
    } catch {
      /* Fail closed: same-origin framing only when config is unavailable. */
    }
  }
  const response = NextResponse.next();
  response.headers.set(
    "Content-Security-Policy",
    `frame-ancestors ${ancestors.join(" ")}`,
  );
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}
export const config = { matcher: "/widget/:path*" };
