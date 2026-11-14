import { Suspense } from "react";
import CustomerChat from "../customer-chat";

export default function WidgetPage() {
  return (
    <Suspense
      fallback={
        <main className="customer-loading">Opening customer support…</main>
      }
    >
      <CustomerChat />
    </Suspense>
  );
}
