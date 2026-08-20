# Local runtime composition verification

## Scope

The packaged local API requires one explicit durable database selection and
Temporal endpoint/task queue. The Compose path selects PostgreSQL, while the
SQLite path remains available for local API contract tests. `poddown-api`
composes the restart-safe episode, command-receipt, approval, and Temporal
client adapters for the selected durable database. A missing database path or
Temporal endpoint fails before the server starts; the entrypoint no longer
silently selects in-memory state.

The Temporal transport bridges the synchronous command port to the async
Temporal SDK, preserves the immutable workflow ID and JSON payload, and
normalizes duplicate workflow starts. SQLite receipt records persist the
workflow ID, command payload, and dispatched state across API restarts.

When a packaged runtime is configured for Temporal, it requires an injected
source-bound workflow snapshot factory. Commands without a factory fail with a
redacted 503 before workflow start; a factory result is checked against the
episode source hash, episode ID, and immutable version and is persisted in the
command payload for replay. The runtime has an explicit reference-fixture
composer when `PODDOWN_WORKFLOW_FIXTURE_ROOT` is set and a worker-owned
configured-content composer when `PODDOWN_CONTENT_CONFIG_ROOT` is set. The
latter binds source, profile, voices, treatment, adaptation, and lexicon
artifacts without preparing at API time; the Temporal preparation activity
performs the source-bound work, persists a secret-free prepared-content
snapshot, and projects the render input for downstream stages. Local modes
require adaptation evidence; live mode requires the explicit provider runtime
and does not fall back to local speech.

The Compose profile remains explicitly `local-contract-only` until a clean
service run is captured. Its API and worker select
`PODDOWN_POSTGRES_DSN`, forward-only migrations, and explicit local
authentication. The worker's `PODDOWN_PUBLICATION_MODE=s3` composes the
existing Boto3 S3 transport with PostgreSQL object references; credentials are
read only from process environment through a `secret://` reference. Concrete
nats-py and Temporal clients remain the service boundaries. The Compose
services are still dependency/readiness boundaries because no clean
live-service run has been performed.

The API Compose service now requires an HTTPS
`PODDOWN_RESOURCE_LINK_BASE_URL`, a non-empty
`PODDOWN_RESOURCE_LINK_SECRET`, and a bounded
`PODDOWN_RESOURCE_LINK_TTL_SECONDS` override (default 300). These values are
interpolated from the operator environment and are not committed as literals.

The worker also exposes the D18 `maintain_objects` cleanup activity only after
the explicit `PODDOWN_OBJECT_MAINTENANCE_ENABLED=1` opt-in and S3 publication
mode check. Its payload is tenant/project scoped and contains no caller-owned
clock, while the cleanup port remains reference-aware and grace-period based.
No scheduler or live cleanup run is implied by this registration.

When the worker is configured for live-provider mode, its evidence recorder
requires `PODDOWN_POSTGRES_DSN` and uses the transaction-bound PostgreSQL usage
ledger. A live worker cannot silently fall back to SQLite; SQLite remains an
explicit local contract adapter.

Configured publication workers similarly pass `PostgresPublicationReceiptRepository`
to `PublishingService`, so receipt replay is durable across worker processes.
An explicitly unconfigured offline worker retains the local process adapter;
configured production content fails closed if PostgreSQL is absent.

The worker registers the one-shot tenant-scoped `OutboxRelayWorkflow`; its
`relay_outbox` activity is registered only when
`PODDOWN_OUTBOX_RELAY_ENABLED=1`. The workflow validates a bounded tenant
batch and applies a three-attempt Temporal retry policy; the activity requires
PostgreSQL and NATS settings, keeps NATS outside workflow authority, and uses
bounded event UUID deduplication plus retry-attempt evidence. No automatic
all-tenant scheduler or live relay run is claimed by this registration.

When `PODDOWN_RESOURCE_LINK_SECRET` and
`PODDOWN_RESOURCE_LINK_BASE_URL` are configured together, the API runtime also
composes the existing `ResourceLinkSigner` with the PostgreSQL object-reference
repository and S3-compatible object store. Partial resource-link configuration
fails closed, and no SQLite/in-memory reader is substituted for the durable
boundary. The signer secret remains process configuration only.

## Evidence

Focused acceptance and unit tests passed for the runtime and snapshot boundary;
the real API-to-Temporal BDD integration passed against a local Temporal server
and worker, and its adjacent API/snapshot/runtime/orchestration/dispatcher set
passed 64 tests in 96.95s. A fresh source-bound regression set passed 99 tests
in 6:13, including the API snapshot/episode paths and long-episode mastering
fix. The new integration proves that the API receipt's
workflow ID is executable and returns a completed deterministic render result.
The outbox workflow BDD scenario passed 4 tests, the runtime/Compose/NATS
registration set passed 32 tests, and four real Temporal wire-serialization
integrations now pass for outbox, fail-closed production-stage error, object
maintenance, and the complete production workflow child/stage boundary. The
latest completed full repository run passed `1560
passed, 1 deselected, 6 warnings` in 27:14 at 80.22% branch coverage.
The runtime/resource composition set passed 39 tests after the latest slice.
Ruff and strict mypy passed for the touched runtime/API modules and the full
source tree.
This is local verification; the hosted runtime remains a separate evidence
tier.

## Explicit deferrals

- PostgreSQL-backed API repositories and RLS are wired in the Compose runtime;
  no live database migration, isolation, or restart run was performed.
- No hosted Temporal worker or clean Compose service run was performed.
- The configured-content composer is a local filesystem configuration boundary;
  it is not yet a hosted content repository or PostgreSQL/S3 system of record.
- The Compose worker selects the checked-in reference fixture and host-local
  speech. Live-provider adapters are guarded and contract-tested, but no live
  request or provider ASR evidence has been produced.
- No live provider request, authenticated UAT, external publication, recovery
  drill, or production-readiness claim follows from this local composition.
