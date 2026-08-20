# Production data-plane boundary verification

## Scope

The current closure branch contains the forward-only PostgreSQL schema runner,
tenant-local RLS transaction setup, restart-safe episode and command-receipt
repositories, approvals, provider-evidence and usage ledger, object references,
transactional outbox, durable publication receipts, concrete Boto3 S3 transport,
and concrete nats-py JetStream transport. S3 objects are content-addressed and require exact bytes,
checksum, byte count, media metadata, and tenant/project scope. Reads through
`DurableS3ObjectStore` require a durable PostgreSQL object reference. The data
plane also records first-seen storage inventory observations and joins them to
tenant-scoped references before conservative orphan deletion.

The Temporal worker boundary exposes this cleanup through the explicit,
fail-closed `maintain_objects` activity. Registration requires S3 mode and the
`PODDOWN_OBJECT_MAINTENANCE_ENABLED=1` operator opt-in; the activity obtains
the observation time inside the worker and returns a compact deletion report.

Legacy migrations backfill source bytes and safe object names, and fail closed
for unscoped outbox rows rather than silently accepting incomplete state. The
publication-receipt migration backfills an explicit episode-version identity and
disclosure object so receipt replay retains its complete immutable audit shape.

`PostgresPublicationReceiptRepository` binds save and replay to the tenant-local
RLS setting, preserves authorization/disclosure/provenance JSON, and rejects an
idempotency key reused for a different immutable receipt. `PublishingService`
accepts this repository through an explicit receipt-store port. The worker passes
it whenever `PODDOWN_POSTGRES_DSN` is configured; a configured production
workflow fails closed without that DSN, while an unconfigured offline worker
retains its explicit local adapter.

The worker's live-provider evidence path now selects the transaction-bound
`PostgresUsageLedger` whenever `PODDOWN_POSTGRES_DSN` is configured. It records
provider evidence and usage as one replay-safe PostgreSQL transaction and fails
closed rather than falling back to SQLite when live mode has no DSN. SQLite
usage remains available only as an explicit local contract adapter.

The transactional outbox has a separate opt-in `relay_outbox` activity and a
one-shot `OutboxRelayWorkflow`. The workflow validates one tenant and bounded
batch limit, invokes the activity with a five-minute start-to-close timeout,
and uses a three-attempt bounded retry policy with a stable tenant/batch
activity identity. The activity uses a tenant-scoped PostgreSQL transaction,
publishes through the async JetStream client on the activity's event loop,
uses the outbox event UUID as the JetStream deduplication identity, and
records failed attempts without marking them published.
`PODDOWN_OUTBOX_RELAY_ENABLED=1` is required to register the activity; no
automatic all-tenant scheduler is provided. NATS is an event-delivery port
and is not workflow authority.

## Current evidence

The focused non-live data-plane acceptance set passed:

```text
The PostgreSQL usage adapter and runtime-selection slice passed **27 focused
BDD/unit tests**. The durable publication-receipt runtime wiring passed **28
focused runtime/publication tests**. The outbox relay/runtime slice passed
**38 focused BDD/integration/unit tests**, including two real local Temporal
wire-serialization runs for the outbox report and fail-closed production-stage
error. The object-maintenance slice now also has a real local Temporal
wire-serialization run proving the worker-clock timestamp and scoped cleanup
report survive the activity boundary; the complete production workflow has a
fourth real local Temporal child/stage wire run. The latest repository-wide
non-live run passed **1560 tests** at **80.22% branch coverage** in 27:14,
including the outbox, object-maintenance, and production-workflow slices. This remains local contract and unit
evidence; it is not a real-service
NATS/PostgreSQL relay or MinIO run.
```

The historical changed-scope package report recorded strict mypy across 15
source files, Ruff format/check, `uv lock --check`, `uv pip check`, and
`git diff --check`. The current reconciliation reran strict mypy across all
`97` source files, Ruff, compileall, and `git diff --check`; the current
environment rejected a fresh `uv lock --check` before execution.

The tests use DB-API, Boto3, and nats-py fakes. No PostgreSQL, MinIO, NATS,
network, provider credential, or external publication was used. No live
service, cross-process transaction, restore drill, or production-readiness
claim follows from these tests.

## Remaining boundary

The runtime can select PostgreSQL explicitly with
`PODDOWN_POSTGRES_DSN`; the Compose API and worker now use that branch and the
worker composes the S3-compatible publication adapter with PostgreSQL object
references. A clean authenticated Compose run with real PostgreSQL, MinIO,
NATS, and Temporal is still required before treating the data plane as
deployed production infrastructure.
