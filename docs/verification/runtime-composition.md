# Local runtime composition verification

The packaged API requires an explicit durable database selection and Temporal
endpoint/task queue. The Compose path selects PostgreSQL; the SQLite path is
available only for explicit local contract tests. Missing database or Temporal
configuration fails before startup.

The Temporal transport preserves immutable workflow identity and JSON payloads,
normalizes duplicate starts, and persists command receipts for replay. A
configured production command carries source/preparation authority; the worker
performs preparation and projects the render snapshot before render, mastering,
QA, packaging, and optional publication stages. Reference-fixture composition
is selected only by explicit fixture configuration.

The worker selects PostgreSQL-backed usage/evidence and publication receipts
when `PODDOWN_POSTGRES_DSN` is configured. It does not silently fall back to
SQLite for live-provider or configured production paths. S3 publication,
resource-link retrieval, outbox relay, and object maintenance are separately
opt-in and fail closed when their durable configuration is incomplete.

Local API-to-Temporal, outbox, object-maintenance, failure-boundary, and full
production-stage wire tests are covered by the repository regression suite.
Disposable local PostgreSQL, MinIO, and NATS integration smokes have also
passed; the exact results are recorded in
[`production-data-plane.md`](production-data-plane.md). This remains local
runtime evidence only: no hosted worker/API restart, live provider request,
external publication, or authenticated UAT has been performed.

On 2026-08-16, a disposable Compose project also exercised the durable restart
boundary. An API create command returned workflow
`poddown-command-8787788e8665cc4ec3c4fdf2a9bca0fa`; the API and worker were
restarted while the status was `rendering`, and the restarted API later
projected `packaged` at progress `0.9` with manifest SHA-256
`32a9ebebbb342b97cdd7c7a837d0a36d6482af6bf52c76bff28c15c85765472b`. The
same run exposed and then verified a PostgreSQL bootstrap race fix: concurrent
API/worker migration initialization is now serialized by a transaction-scoped
advisory lock. The Compose project and all volumes were removed afterward.
This is disposable local recovery evidence, not hosted recovery or production
readiness evidence.
