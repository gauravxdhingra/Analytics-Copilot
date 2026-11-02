---
name: threat-model
description: Update the threat model for a new or changed component, tool, data flow or trust boundary. Use whenever the attack surface changes.
argument-hint: "<component or change>"
---

Threat model update for: $ARGUMENTS

Read ARCHITECTURE.md §5 and FinAnalytics.md §10. Existing models live in `docs/threat-model/`.

1. **Describe the change**: components, data flows, trust boundaries crossed, data classes handled, who can reach it. Add or update a small data-flow diagram (mermaid).
2. **STRIDE** for the non-LLM surface: one row per threat per element (spoofing, tampering, repudiation, information disclosure, denial of service, elevation of privilege).
3. **LLM-specific threats**, mapped to OWASP Top 10 for LLM Applications and MITRE ATLAS **[VERIFY current versions]**: prompt injection (direct and via data), insecure output handling, excessive agency, sensitive information disclosure, supply chain (tool manifests, models, dependencies), unbounded consumption.
4. For each threat: existing control (cite the spec section), residual risk, and the **test that proves the control** (red-team case, leakage test, contract test). A control with no test is recorded as a gap.
5. **Abuse cases** become red-team test ideas; hand them to the `eval-engineer` agent.
6. Write or update `docs/threat-model/<component>.md`. Then run the `security-reviewer` agent on the same scope and reconcile its findings.
7. List open gaps at the top. Do not commit.
