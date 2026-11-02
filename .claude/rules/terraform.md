---
paths:
  - "deploy/**"
  - "**/*.tf"
  - "**/Dockerfile"
  - "**/docker-compose*.yml"
---

# Infrastructure

Spec: ARCHITECTURE.md §6, §7, §12, §13.

- AWS is primary, Azure second; modules per cloud expose **matching interfaces**. Every service is a container image that also runs under Docker Compose.
- Never run `terraform apply` or `destroy` from this environment. Produce and review plans; a human applies.
- No long-lived credentials. IAM roles (ECS task roles) and managed identities only. Secrets in Secrets Manager / Key Vault, never in variables files.
- Containers: non-root, read-only root filesystem, pinned base image digest, no `latest` tags. Images are scanned, SBOM'd and signed.
- Network: private subnets, default-deny security groups / NSGs, VPC / private endpoints for model and data services, TLS everywhere.
- Encryption with customer-managed keys at rest. Object Lock / immutable storage for the audit archive.
- Cost-allocation tags are required on every resource (enforce in a module, not by convention).
- Interactive services: minimum 2 tasks across AZs in prod, health checks, timeouts at every hop. Batch and event work uses jobs or functions.
