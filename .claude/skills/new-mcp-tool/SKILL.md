---
name: new-mcp-tool
description: Scaffold a new MCP tool end to end (schema, handler, policy entry, contract test, trace span, docs). Use when adding or changing a tool on the analytics, operations, knowledge or drafts server.
argument-hint: "<server>.<tool_name> <READ|ANALYZE|DRAFT>"
---

Add tool: $ARGUMENTS

Before writing code, read the rule file `.claude/rules/mcp-and-policy.md` and FinAnalytics.md §5.1–5.2.

Hard stops:
- If the tool would mutate state, execute a payment/configuration/match, or approve anything, stop and tell the user it violates non-negotiable 1.
- `drafts` tools may only create and submit drafts.

Steps (tests first):
1. **Contract test**: write failing tests for input validation, output schema, every error code, and the denial case (`PolicyDenied` with reason code).
2. **Schemas** in `packages/schemas/`: Pydantic input and output models; derive the JSON schema from them. Include `purpose` where the spec requires it.
3. **Handler** in `mcp/<server>/`: validate the audience-bound token, call the underlying system as the user, return typed output with `Evidence` (source tool, source ref, snapshot time). Map errors to codes; never return raw upstream errors.
4. **Taint**: identify fields that carry untrusted text and mark them so the gateway wraps them.
5. **Policy**: add the tool to the relevant agents' allowlists in the versioned policy YAML, with constraints (row caps, runtime, purpose). Bump the policy version.
6. **Trace**: emit a `tool.call` span with tool, server, policy decision, args hash, row count. No raw prompts or restricted values in attributes.
7. **Docs**: update the tool list in FinAnalytics.md §5.1 and any affected sequence in ARCHITECTURE.md.
8. Run `/threat-model` for the new tool if it reads tainted data or touches the Query API.
9. Run the tests and report results honestly.
