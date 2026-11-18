import type { Answer, Draft } from "../src/lib/types";

export const answered: Answer = {
  status: "answered",
  text: "Halcyon GmbH has 12 booked transactions. As of 2026-06-30.",
  table: { columns: ["entity", "count"], rows: [["HAL-DE", 12]], row_count: 1, truncated: false, as_of: "2026-06-30" },
  sql: "SELECT entity_id, count(*) FROM treasury.transactions WHERE status = %s GROUP BY 1",
  params: ["booked"],
  refusal_reason: null,
  clarification: null,
  caveats: ["Data is as of 2026-06-30."],
  agent_result: {
    run_id: "r1",
    claims: [
      {
        text: "12 booked transactions",
        confidence: 1,
        evidence: [
          {
            evidence_id: "e1",
            provenance: "system_state",
            source_tool: "analytics.run_validated_select",
            source_ref: "q:abc123",
            snapshot_at: "2026-06-30T00:00:00Z",
            excerpt: null,
            trace_span_id: null,
          },
        ],
      },
    ],
    open_questions: [],
    caveats: [],
    versions: { catalogue: "v1", models: "gemini" },
  },
  steps: ["route", "plan", "verify", "execute", "narrate"],
  draft_id: null,
  draft_error: null,
  trace_id: "a".repeat(32),
};

export const refused: Answer = {
  ...answered,
  status: "refused",
  text: "That is outside what I can help with.",
  refusal_reason: "unsupported",
  table: null,
  sql: null,
  agent_result: null,
  trace_id: null,
};

export const submitted: Draft = {
  draft_id: "00000000-0000-4000-8000-000000000001",
  kind: "case_file",
  entity_id: "HAL-DE",
  title: "Review payments near the limit",
  payload: { summary: "Three payments are near the limit.", recommended_actions: ["Check limits"], evidence: [] },
  status: "submitted",
  created_by: "u1",
  created_at: "2026-06-30T10:00:00Z",
  submitted_by: "u1",
  decided_by: null,
  decision_comment: null,
  trace_id: null,
};
