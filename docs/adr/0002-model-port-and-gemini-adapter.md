# 0002. Provider-neutral model port with a Gemini default adapter

- **Status:** Accepted
- **Date:** 2026-11-18
- **Deciders:** Owner
- **Supersedes:** none (narrows D3 for the showcase; see Consequences)
- **Spec references:** Analytics Copilot.md §3, §12, D3; ARCHITECTURE.md §10, §15 #2

## Context

D3 says models are hosted, through Bedrock on AWS and AI Foundry on Azure, and that a laptop calls hosted APIs with developer credentials. The showcase runs on a laptop with the Gemini API. Provider coupling inside graph nodes would make the Bedrock and Foundry paths a rewrite, and it would make evals non-deterministic and costly in CI.

Principle 8 requires reproducible evals. Principle 7 requires pinned model versions and no raw prompts in span attributes.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Call the Gemini SDK directly from nodes | Least code | Provider lock-in; hard to test; no replay |
| B. A framework wrapper (e.g. LangChain chat models) used by nodes | Many providers available | Framework types leak into domain code; abstractions change between releases |
| C. Own port (`ModelClient`) over Pydantic request/response models, with adapters | Provider-neutral; testable; replay for evals; matches "every node reads and writes Pydantic models" | We maintain a small interface |

## Decision

Define a `ModelClient` port (C) that takes and returns Pydantic models and covers structured output, tool calls, usage, latency and a mapped error taxonomy. Ship three adapters: `GeminiClient` (default live adapter, `google-genai` SDK), `ReplayClient` (recorded responses keyed by a request hash, used by CI and evals) and `FakeClient` (scripted, for unit tests). Config selects the adapter. Bedrock and Foundry adapters are future work behind the same port.

Rules the adapter enforces: per-call timeout and retry with backoff, a mapped fallback on rate-limit or provider error, structured-output schemas for plan and routing nodes, usage and cost recorded on the span, prompts and completions kept out of span attributes (stored redacted in the decision store).

The eval judge uses a different model or tier from the generator where possible.

## Consequences

- Positive: evals are deterministic and free in CI; provider swap is one adapter; graph nodes stay provider-free.
- Negative / costs: recordings must be regenerated when prompts or model versions change, which the eval gate already requires; the port must be kept minimal or it becomes its own framework.
- Follow-ups:
  - D3 says the product runs on Bedrock and Foundry in deployment. This ADR adds Gemini as the laptop and showcase provider. If accepted, update D3 and ARCHITECTURE.md §10 and §15 #2 wording, then run `spec-guardian`.
  - Port contract tests run against every adapter.

## Verification

- The same contract test suite passes for Fake, Replay and Gemini adapters (Gemini behind a live-test marker, excluded from the default gate).
- A grep-based test fails the build if any module outside `adapters/models/` imports the provider SDK.
- Replay hit rate is 100% in CI; a miss fails the run.

## Open items

- Checked 2026-11-18 against ai.google.dev: `gemini-3.8-flash` is the stable Flash model, and Pro exists only as the preview `gemini-3.1-pro-preview`. The adapter defaults every role to the stable Flash with `gemini-3.6-flash` as the fallback, and every name is overridable by environment. **[VERIFY]** again at each pin, along with structured-output and function-calling behaviour and rate limits. The live smoke test (`-m live`) has not been run: no API key was available when it was built.
- **[VERIFY]** Provider data-use terms for the API tier used (training and retention). The showcase sends only synthetic data, which is the mitigation; production needs the terms in ARCHITECTURE.md §10 (no-training, zero retention).
- **[TWEAK]** Which model class serves routing, planning and narrative.
