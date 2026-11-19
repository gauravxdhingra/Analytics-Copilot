// Mirrors the BFF's JSON. The browser never sees a token, only these shapes.

export interface Me {
  username: string;
  roles: string[];
  entities: string[];
  tenant_id: string;
  acr: string | null;
  strong_authentication: boolean;
}

export type Provenance = "system_state" | "document" | "inference" | "unknown";

export interface Evidence {
  evidence_id: string;
  provenance: Provenance;
  source_tool: string | null;
  source_ref: string;
  snapshot_at: string | null;
  excerpt: string | null;
  trace_span_id: string | null;
}

export interface Claim {
  text: string;
  evidence: Evidence[];
  confidence: number;
}

export interface AnswerTable {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  as_of: string | null;
}

export interface AgentResult {
  run_id: string;
  claims: Claim[];
  open_questions: string[];
  caveats: string[];
  versions: Record<string, string>;
}

export type AnswerStatus = "answered" | "refused" | "clarify" | "failed";

export interface Answer {
  status: AnswerStatus;
  text: string | null;
  table: AnswerTable | null;
  sql: string | null;
  params: unknown[];
  refusal_reason: string | null;
  clarification: string | null;
  caveats: string[];
  agent_result: AgentResult | null;
  steps: string[];
  draft_id: string | null;
  draft_error: string | null;
  trace_id: string | null;
}

export type AskEvent =
  | { event: "step"; data: { step: string } }
  | { event: "answer"; data: Answer }
  | { event: "error"; data: { error_code: string; reason?: string } }
  | { event: "done"; data: Record<string, never> };

export type DraftStatus = "draft" | "submitted" | "approved" | "rejected";

export interface Draft {
  draft_id: string;
  kind: string;
  entity_id: string;
  title: string;
  payload: {
    summary: string;
    recommended_actions: string[];
    evidence: Array<{ source_tool: string; source_ref: string }>;
  };
  status: DraftStatus;
  created_by: string;
  created_at: string;
  submitted_by: string | null;
  decided_by: string | null;
  decision_comment: string | null;
  trace_id: string | null;
}

export interface Span {
  span_id: string;
  parent_id: string | null;
  name: string;
  start_us: number;
  duration_us: number;
  attributes: Record<string, string | number | boolean>;
}

export interface HistoryTurn {
  conversation_id: string;
  question: string;
  status: string;
  summary: string;
  trace_id: string | null;
  created_at: string;
}

export interface MemoryItemView {
  pref_id: string;
  kind: "preference" | "saved_definition";
  name: string | null;
  text: string;
  created_at: string;
}

export interface NewMemoryItem {
  kind: "preference" | "saved_definition";
  name?: string;
  text: string;
}
