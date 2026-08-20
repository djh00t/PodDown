# Specification: Production Readiness

**Status:** Local operational contracts verified; release gate remains open
until hosted end-to-end evidence exists

## Goal

Operate PodDown securely and predictably as a small SaaS-ready service without
premature orchestration complexity.

## Runtime

One Python distribution provides FastAPI API and Temporal worker entry points.
Docker Compose runs PostgreSQL, Temporal, NATS JetStream, S3-compatible storage,
API and workers. Health endpoints distinguish liveness, readiness and dependency
degradation. Kubernetes is excluded until measured operational need exists.

## Operations

Structured logs, traces and metrics correlate tenant-safe IDs across API,
workflow, activities and provider calls. SLOs cover API availability, queue age,
render completion, provider failure, QA repair rate and cost variance. Alerts are
actionable and exclude source/script/audio content.

Secrets use environment-backed secret injection and rotation. Encryption applies
in transit and at rest. Retention policies independently cover sources, audio,
transcripts, provider payloads, logs and consent evidence. Deletion is tenant-scoped,
audited and blocked where immutable consent/publication evidence must be retained.

Database migrations are forward tested with rollback/restore procedures. Object
storage and PostgreSQL backup restoration are exercised. Releases use SemVer,
Conventional Commits, semantic-release, SBOM, dependency/vulnerability scanning,
signed artifacts and staged deployment.

## Acceptance behavior

1. A clean Docker Compose environment passes the end-to-end robotics episode test.
2. Worker/API restart during each major stage resumes without corruption.
3. Backup restoration reproduces authoritative records and artifact checksums.
4. Tenant isolation and secret-redaction security tests pass.
5. Provider outage and budget exhaustion produce bounded, observable failure.
6. Release artifacts are reproducible, scanned and traceable to source commit.
7. Load tests establish capacity and scaling thresholds before launch.

## Production-closure evidence and recovery contract

Operational evidence distinguishes local validation, host-local listening demos,
provider-live rendering/ASR, storage publication and external publication. Reports
include provider request IDs, model IDs, hashes, Decimal estimated/reconciled costs,
latency, retries, provenance and exact package checksums without raw source, audio,
provider payloads or credentials. Clean-checkout verification must demonstrate API,
worker, storage and workflow restart recovery, replay idempotency, failed-segment
rerendering, 100% critical-token accuracy and an honest evidence classification.
