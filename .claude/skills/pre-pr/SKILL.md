---
name: pre-pr
description: Pre-PR checklist. Runs quality gates and the relevant reviewer agents on the current changes, then summarizes blockers. Use before opening a pull request or asking for review.
disable-model-invocation: true
---

Prepare the current branch for review. Do not commit, push or open a PR.

1. **Scope**: `git status` and `git diff` against the main branch. Classify the changed paths: docs, python, mcp/policy, sql/query-api/catalogue, evals, infra, frontend.
2. **Quality gates** (run what exists; if a target is missing, say so, do not invent results): `make lint`, `make typecheck`, `make test`. Run `make eval` when prompts, models, catalogue, policy or eval sets changed.
3. **Reviewers, in parallel, only for the paths that apply**:
   - docs or ADRs changed → `spec-guardian`
   - mcp, policy, auth, guardrails, BFF, query-api → `security-reviewer`
   - verifier, catalogue, analysis, recon, detectors → `data-correctness-reviewer`
   - deploy, Dockerfiles, CI → `infra-reviewer`
   - new behaviour without tests → `test-engineer` to propose the missing tests
4. **Checklist** (answer yes/no/NA with a pointer):
   - Tests or evals added first for new behaviour?
   - Any non-negotiable touched? If so, is there an ADR?
   - Trace spans and policy entries present for new tools?
   - Threat model updated if the attack surface changed?
   - Docs and decision references still consistent?
   - No secrets, real data or employer-specific names?
5. **Report**: blockers first, then should-fix, then notes. Include a suggested Conventional Commit message and a PR description draft (summary, why, test evidence, risks). Be honest about anything not run.
