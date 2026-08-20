# Local production data-plane verification

The reconciled overlay contains forward-only PostgreSQL migrations, tenant-local
RLS transaction setup, episode and command repositories, approvals, provider
evidence and usage ledgers, object references, transactional outbox, durable
publication receipts, Boto3 S3 transport, and NATS JetStream transport ports.

Object references are tenant/project scoped and content addressed. Exact bytes,
checksum, byte count, media metadata, and idempotency are validated. Inventory
observations join durable references before conservative orphan deletion; the
Temporal object-maintenance activity is explicit, scoped, and worker-clocked.
The outbox relay uses stable event identity, bounded batches, retry evidence,
and does not make NATS the workflow authority.

The local BDD/unit/integration suite covers repository scope, migrations, RLS
contracts, usage/evidence replay, S3 validation, outbox behavior, and Temporal
wire serialization. Most tests use injected DB-API/Boto/NATS fakes and local
Temporal test infrastructure. On 2026-08-16, the opt-in PostgreSQL integration
slice passed 3 tests against PostgreSQL 16.4 in a disposable local container,
using a non-superuser, non-BYPASSRLS application role; the optional pooled
connection leakage test remained skipped because `psycopg_pool` is not
installed. The opt-in MinIO integration also passed 3 tests against the pinned
local MinIO image in a disposable tmpfs-backed container, and the opt-in NATS
integration passed 2 tests against the pinned NATS 2.10.20 image in a
disposable tmpfs-backed container. A D18 runtime-catalog test also passed for
tenant-scoped object-reference/inventory reconciliation and conservative orphan
cleanup. No cross-process transaction, restore, or hosted recovery drill has
been performed. A disposable Compose restart drill did verify concurrent
API/worker PostgreSQL bootstrap serialization and Temporal package survival
across API and worker restart; this does not replace a database/object-store
outage or restore drill.
