# 0003. Keycloak OIDC, audience-bound token exchange, and database-enforced scope

- **Status:** Accepted
- **Date:** 2026-11-18
- **Deciders:** Owner
- **Supersedes:** none
- **Spec references:** Analytics Copilot.md §10, D6, D8; ARCHITECTURE.md §5.1, §5.2, §7.3, §8.3, §15 #12

## Context

The spec fixes the identity design: OIDC login through an IdP, short-lived audience-bound tokens at every hop via RFC 8693, data authorization owned by the underlying system (D6), tool policy owned by the gateway, and approval as a separate service. Open item #12 asks which IdP the showcase uses. The laptop profile in §7.3 already lists Keycloak.

Non-negotiables 3 and 6 apply: the copilot adds controls but never replaces data authorization, and it fails closed.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Keycloak container | Runs on a laptop; supports OIDC, PKCE, step-up and token exchange; realm exported as code | Token exchange is a feature that must be enabled and checked against the version in use; heavier than a stub |
| B. Cognito | Matches the AWS target | Needs a cloud account to run; weaker token-exchange story; cannot run offline |
| C. Hand-rolled or mock IdP | Trivial | Demonstrates nothing; fails the "authentication and authorization at least" goal |

## Decision

Use Keycloak in Docker Compose as the showcase IdP (A) with a seeded realm for Halcyon Group, and implement the flow in ARCHITECTURE.md §5.1 as written:

- **Authentication:** OIDC Authorization Code with PKCE. The BFF holds the session in an httpOnly, SameSite cookie; the browser never holds an access token. The BFF refreshes tokens and calls the IdP end-session endpoint on logout.
- **Hops:** RFC 8693 token exchange for each audience (orchestrator, analytics MCP server, Query API). Tokens are never passed through. Services validate issuer, audience, scope and expiry.
- **Authorization layers:** (1) role and capability checks at the BFF and orchestrator; (2) tool policy at the gateway, by tier, role and entity scope, from versioned YAML; (3) data scope in Postgres through `SET LOCAL ROLE` plus row-level security keyed on the user, so the Query API runs as the requesting user (D6).
- **Approval:** a separate service with its own audience and scope. The copilot can create a draft but holds no scope to approve it. The approver must differ from the drafter (separation of duties). Approval requires IdP step-up authentication.
- **Fail closed:** if the IdP, gateway or token validation is unavailable, the request is refused with a mapped code.

Seeded users: an analyst for entity A, an analyst for entity B, a manager with both, and an approver. Tenancy (D8) is pooled; every token carries a tenant id.

## Consequences

- Positive: the real flow is exercised, not stubbed; authorization failures are testable at each layer; the pattern ports to Cognito or Entra by configuration.
- Negative / costs: Keycloak setup (realm, token-exchange permissions, step-up flow) takes real effort and sits first in the build order; local startup is slower.
- Residual risk (found building the reference Query API): the Query API's login role can `SET ROLE` to any user role, so a `SET ROLE <other user>` statement would cross users if it ever reached the database. The SELECT-only route (`set_config`) is closed by revoking its execute privilege, and the server-side SQL guard rejects every non-SELECT statement. The guard is therefore load-bearing for this boundary and must keep its negative tests. Record in the threat model (V1-17). A production system could remove the exposure with one login role per user or a connection broker.
- Follow-ups:
  - Resolves ARCHITECTURE.md §15 #12 for the showcase. If accepted, update that entry and S0 W4.
  - PgBouncer or RDS Proxy behaviour with `SET LOCAL` (ARCHITECTURE.md §8.3) does not apply to the laptop profile but is tracked for deployment.
  - Update the threat model for the new trust boundaries.

## Verification

Automated tests, also placed in the eval and red-team sets:

- Leakage: a user scoped to entity A gets a denial or empty result for entity B, at the gateway and again directly at the database.
- Prompt injection inside a tool result that tries to widen scope is blocked by the gate and visible in the trace.
- Cache isolation: identical questions from two users never share a cached result.
- Expired, wrong-audience and wrong-issuer tokens are rejected at every hop.
- The copilot service credential cannot approve a draft; the drafter cannot approve their own.
- With Keycloak stopped, every endpoint refuses.

## Open items

- Verified 2026-11-18 on Keycloak 26.8.2: standard token exchange works per hop. A client may only obtain an audience through a matching optional client scope (`aud-<client>`), which is how the realm limits each client to its allowed hops. The request must carry both `audience` and `scope=aud-<client>`. Integration tests in `packages/auth/tests/integration/` cover this. **[VERIFY]** again when the Keycloak version is bumped.
- **[VERIFY]** Current MCP authorization specification revision (ARCHITECTURE.md §5.1).
- **[CONFIRM]** Whether the real underlying system enforces row scope (D6). The reference Query API does, so the showcase is unaffected; this stays open for a real integration.
