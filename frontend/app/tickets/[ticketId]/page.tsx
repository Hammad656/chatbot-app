import { Suspense } from "react";
import ResolveAI from "../../resolve/dashboard";

export default function TicketPage() {
  return (
    <Suspense fallback={<p>Loading ticket…</p>}>
      <ResolveAI initialView="tickets" />
    </Suspense>
  );
}
