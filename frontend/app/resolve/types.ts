export type User = {
  id: string;
  email: string;
  full_name: string;
  active_workspace_id: string | null;
  role: "owner" | "admin" | "agent" | null;
};
export type AuthSession = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
};
export type Workspace = {
  id: string;
  name: string;
  ai_mode: "local" | "gemini";
  role: "owner" | "admin" | "agent";
};
export type Source = {
  id: number;
  document_id: string;
  filename: string;
  section: string;
  position: number;
  text: string;
};
export type Document = {
  id: string;
  filename: string;
  status: string;
  chunk_count: number;
  attempts: number;
  error: string | null;
};
export type Ticket = {
  id: string;
  subject: string;
  customer_email: string;
  order_reference: string | null;
  status: string;
  category: string;
  priority: string;
  queue: string;
  revision: number;
  created_at: string;
  updated_at: string;
  last_error: string | null;
};
export type Run = {
  id: string;
  status: string;
  mode: string;
  model: string;
  prompt_version: string;
  tokens: number;
  duration_ms: number;
  error: string | null;
};
export type TicketDetail = Ticket & {
  description: string;
  draft: string | null;
  proposed_action: {
    kind?: string;
    rationale?: string;
    reference?: string;
    amount_cents?: number;
    currency?: string;
  };
  sources: Source[];
  tool_calls: {
    name: string;
    arguments: Record<string, unknown>;
    result: Record<string, unknown>;
  }[];
  messages: { id: string; role: string; content: string; created_at: string }[];
  decisions: {
    id: string;
    decision: string;
    action: string;
    reply: string;
    actor_id: string;
    result: Record<string, unknown>;
    created_at: string;
  }[];
  runs: Run[];
};
export type Order = {
  id: string;
  reference: string;
  customer_email: string;
  item: string;
  status: string;
  amount_cents: number;
  currency: string;
  refundable: boolean;
  replaceable: boolean;
};
export type Audit = {
  id: string;
  event: string;
  ticket_id: string | null;
  actor_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
};
export type Answer = {
  id: string;
  thread_id: string | null;
  question: string;
  answer: string;
  sources: Source[];
  mode: string;
  created_at: string;
};
export type Snapshot = {
  workspace: Workspace;
  tickets: Ticket[];
  documents: Document[];
  orders: Order[];
  audit: Audit[];
  answers: Answer[];
  threads: { id: string; title: string; created_at: string }[];
  members: { email: string; role: string }[];
  metrics: {
    tickets: number;
    statuses: Record<string, number>;
    recent_runs: number;
    failed_runs: number;
    recent_tokens: number;
    average_duration_ms: number;
    feedback: Record<string, number>;
  };
};
