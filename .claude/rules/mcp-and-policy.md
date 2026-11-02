---
paths:
  - "mcp/**"
  - "services/policy-gateway/**"
  - "services/orchestrator/**"
---

# MCP servers, policy gateway and orchestrator

Spec: FinAnalytics.md §5.1–5.2 and §3; ARCHITECTURE.md §3, §5.

- The four servers are `analytics`, `operations`, `knowledge`, `drafts`. They are internal-only and are reached only through the policy gateway (PL1).
- Tool tiers are `READ`, `ANALYZE`, `DRAFT`. **Never add a tool that mutates or executes.** `drafts` can create and submit drafts, never approve.
- Every tool has: a JSON-schema input, an output schema, a policy entry in the versioned YAML, a contract test, and a trace span (`tool.call`). Use the `/new-mcp-tool` skill.
- Servers validate the audience-bound user token themselves (audience, scope, expiry). No token pass-through: exchange for the next audience (RFC 8693).
- Tool descriptions and manifests are code. Review them like code; a poisoned description is an attack vector.
- Data returned from tools may be tainted. Wrap tainted fields in `<untrusted source="…">` and attach taint labels in post-processing.
- Extraction agents get **no tools**. DRAFT tools reject arguments copied verbatim from tainted content unless a deterministic validator confirms them.
- The gateway does not decide data scope (D6). Do not add row or column filtering logic to it.
- Fail closed: a denial returns `PolicyDenied(reason_code)`, and the reason code appears in the trace.
- Orchestrator caps (`MAX_ITERATIONS`, `MAX_TOOL_CALLS`, `MAX_REPAIR_ATTEMPTS`, cost, wall clock) are constants in code; derive the LangGraph `recursion_limit` from them.
