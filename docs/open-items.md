# Open items

Every unresolved `[DECIDE]`, `[VERIFY]` and `[CONFIRM]` marker in the two spec docs, plus untagged open questions. Built from the S0 W2 audit on 2026-11-04. Line numbers refer to the files as of that date; re-run `spec-guardian` after edits. Resolve an item with an ADR (`/adr`), then remove its marker.

Files: **PS** = [Analytics Copilot.md](../Analytics%20Copilot.md), **ARCH** = [ARCHITECTURE.md](../ARCHITECTURE.md).

## [DECIDE]: needs a decision

| Where | Question | Needed by |
|---|---|---|
| PS:80 | Per-question cost budget (KPI and release gate). Proposed at $0.01 in [ADR 0006](adr/0006-release-gates-and-budgets.md), awaiting acceptance | S2 baselines, S4 |
| PS:178 | Final name of the fictional company (working name Halcyon Group; D1 says provisional) | S1 |
| PS:408 | Semantic-layer export target: dbt MetricFlow or Cube | S4 |
| PS:890 | Supported UI languages | S4 (also affects intent training data and language-ID rail) |
| PS:896 | Tenancy: pooled with silo option. D8 says Decided but the marker remains | S0 ADR |

## [CONFIRM]: needs the owner of the underlying system

| Where | Question | Needed by |
|---|---|---|
| ARCH:197 | Does the underlying system enforce row-level scope for the Query API (e.g. Postgres RLS keyed on user identity)? Backs D6 | S3 |
| ARCH:258 | Host application's availability SLA (default 99.9% monthly if none) | S5 |
| ARCH:370 | Does the real Query API match the §8.2 contract? Backs D4 | S3 |

## [VERIFY]: check against current vendor docs and spec versions

| Where | What to check | Needed by |
|---|---|---|
| PS:233 | MCP feature use against the current spec revision (elicitation, structured output, resources, progress, transport) | S3 |
| ARCH:190 | Current MCP authorization spec revision | S3 |
| PS:512 | Current BIRD / Spider 2.0 versions | S4 |
| PS:831 | DeepEval API and metric names | S2 |
| PS:928 | SOC 2, ISO 27001, NIST AI RMF, EU AI Act versions | S5 |
| ARCH:227 | OWASP LLM Top 10 and MITRE ATLAS versions (also needed by the S0 threat model) | S0 |
| ARCH:241 | Function response-streaming and gateway timeout limits | S5 |
| ARCH:419 | PgBouncer / RDS Proxy behaviour with `SET LOCAL` and pinning | S1 (Query API reference) |
| ARCH:506 | Hosted-model provider terms: no-training, zero retention | S3 |
| ARCH:514 | Status of OTel GenAI semantic conventions | S3 |

## [ADR]: queued decisions

| Where | Decision |
|---|---|
| ARCH:286, 305 | Graph database (Neptune / Cosmos DB vs Neo4j everywhere) |
| ARCH:527 | DR strategy and RPO/RTO (15 min / 4 h): not in the §15 backlog |
| ARCH §15 #11 | Durable workflow engine |
| ARCH §15 #12 | Identity provider for the showcase |
| PS D11 | License and public-repository policy (repo is already public, no LICENSE file) |

## Untagged open questions

| Where | Question |
|---|---|
| PS:986, 988 | D4 and D6 say "confirm" in plain text; they correspond to the [CONFIRM] items above |
| PS:969 | Risk row "Underlying system cannot enforce row scope" has no marker |
| ARCH §7.3, §7.4 | Keycloak and Neo4j (laptop) and Step Functions / Durable Functions are defaults pending ADRs #10–12 |

## Spec consistency findings awaiting an owner decision

From the W2 audit. These change spec meaning, so they are not applied automatically.

| Severity | Where | Issue | Recommended fix |
|---|---|---|---|
| High | ARCH:468 vs PS D4 | §8.3 says direct DB access "stays a fallback", which moves data authorization into the copilot (non-negotiable 3). D4 says it was rejected | Delete the fallback sentence, or make it an ADR-backed exception stated in D4 and D6 |
| High | PS:12 | Names a private notes file and says it holds employer-specific names, in a public repo | Reword to "private source notes, kept out of the repo"; drop the filename |
| Medium | PS D8 vs PS:896 | Decided vs `[DECIDE]` | Write the ADR, drop the marker |
| Medium | PS D5 vs PS:408 | Export adapter called decided; target format open | Reword D5: "export adapter, target format open" |
| Medium | PS D1 vs PS:178; ARCH §15 | "Provisional" vs decided; D1 not queued for an ADR | Say "name provisional"; add D1 to §15 or mark exempt |
| Medium | PS D10 | Status note mixes in unrelated open items (budget, languages) | Move to "Other open items" |
| Medium | ARCH:159–169 vs PS §6.5 | Latency budget omits narrative, output checks and repair loops; stage maxima sum near 19 s and execution can use 30 s | Add rows; state whether retries are inside the 60 s p95 |
| Medium | PS:455, 465, 746 vs ARCH:197 | Verifier "sensitive column" and output "scope re-check" read as copilot-side authorization | Label as defence in depth; define "scope re-check"; confirm in D6 |
| Medium | ARCH:201 | Document ACLs enforced by the copilot; D6 covers only Query API data | State in D6/ADR that source-ACL replication is accepted |
| Medium | PS §10.5 vs ARCH:191 | "Workload identity with mTLS" vs "TLS between services" | Pick one |
| Medium | ARCH:527 | `[ADR]` on DR targets with no backlog entry | Add to §15 or drop the tag |
| Low | PS:661 | Detector ids D1–D10 collide with decision ids D1–D11 | Rename detectors `DET1–DET10` |
| Low | PS:320 / PS:291 | Docs never say a failed ledger write denies the call | Add one line to PS:291 |
| Low | PS:526, 331 | Dashboard publish and ApprovalSink webhook sit near "no config changes" | Add one-liner: publishing is an approved draft record only |
| Low | PS:170 vs ARCH:493–502 | Model-role tables differ | Add a mapping note |
| Low | PS:80, 865 | Cost gate depends on an undecided budget | Note dependency or set a [TWEAK] placeholder |
| Low | PS:730, 890 | Multilingual intent data before languages are decided | English only until decided |
