# M3 Durable Persistence and Metering Plan

> Execute on `codex/m3-durable-persistence`, directly stacked on PR #18
> (`codex/m3-tenant-object-storage`). Keep the adapter local and deterministic;
> do not claim PostgreSQL, hosted auth, NATS, or provider-invoice reconciliation.

## Objective

Add a restart-safe local persistence boundary for the episode API and an
append-only usage/cost ledger without changing the existing lifecycle, API
problem, object-storage, or provider contracts. The local adapter is the
repeatable demo path; a PostgreSQL/Alembic implementation remains a later
production milestone behind the same ports.

## Scope and ownership

Owned files:

- `src/poddown/persistence.py`
- `src/poddown/api/__init__.py` only for optional persistent app wiring
- `tests/features/durable_persistence.feature`
- `tests/bdd/test_durable_persistence.py`
- `tests/unit/test_persistence.py`
- `tests/integration/test_durable_persistence.py`
- `docs/verification/durable-persistence.md`
- this plan

Shared coordinator file:

- `docs/planning-traceability.md`

No provider adapter, audio workflow, object-storage implementation, or existing
in-memory behavior is rewritten. The default API remains deterministic in-memory
when no database path is supplied.

## Contract decisions

- `SQLiteEpisodeRepository` implements the existing `EpisodeRepository` port
  with one durable SQLite file, WAL mode, foreign-key enforcement, and
  transaction-scoped optimistic version updates.
- Episode identity and idempotency are tenant-scoped. A replay with the same
  tenant/key/fingerprint returns the original record; a conflicting fingerprint
  raises the existing `IdempotencyConflict`; a wrong-tenant lookup is the
  existing `EpisodeNotFound`.
- `SQLiteCommandDispatcher` persists API command receipts with the same
  tenant-scoped idempotency and conflict semantics as the in-memory dispatcher.
- `SQLiteUsageLedger` appends immutable `UsageEvent` records keyed by
  `(tenant_id, provider_request_id)`. Exact replay returns the original event;
  a conflicting event fails closed; a wrong-tenant read is not found.
- Usage records contain tenant, project, job, provider request, operation,
  units, currency, estimated cost, reconciled cost, and UTC creation time.
  Decimal money values are stored as canonical strings. Metering never controls
  lifecycle correctness.
- Source bytes, credentials, provider secrets, and raw audio are never stored
  in the persistence or metering tables. Only existing checksums, bounded
  metadata, redacted failure fields, and structured usage are persisted.
- `create_app(database_path=...)` opts into the durable repository and command
  receipt adapter. Existing callers of `create_app()` keep the offline in-memory
  default.

## Acceptance behaviors

1. A created episode remains readable after the application and repository are
   reconstructed against the same SQLite file.
2. Concurrent/repeated create requests with one tenant-scoped idempotency key
   produce one episode and reject conflicting fingerprints.
3. Cross-tenant reads and idempotency keys cannot observe or mutate another
   tenant's episode or command receipt.
4. Optimistic version updates are atomic; stale versions fail with
   `VersionConflict` and illegal lifecycle transitions remain rejected.
5. API command receipts survive restart and preserve exact command identity.
6. Usage events are immutable, append-only, idempotent by provider request,
   tenant-scoped, and preserve exact units and Decimal cost values.
7. Tests use temporary local files only. No database server, provider, network,
   credentials, cloud spend, or invoice system is required.

## TDD execution order

### Task 1: Write BDD, unit, and integration contracts first

- Add BDD scenarios for restart recovery, tenant isolation, idempotent replay,
  stale-version rejection, command receipt replay, usage event replay/conflict,
  and provider-free local operation.
- Add unit tests for serialization, schema validation, Decimal round trips,
  immutable identity checks, typed conflicts, and malformed rows.
- Add integration tests using fresh repository/ledger/dispatcher instances over
  the same temporary SQLite file, including concurrent idempotent creation.
- Capture RED before the production module exists.

### Task 2: Implement the durable local adapters

- Create the schema with explicit tables and uniqueness constraints.
- Implement transaction-scoped create/get/replace operations using only
  parameterized SQL and tenant predicates.
- Implement command receipt persistence and the append-only usage ledger.
- Keep all serialization deterministic and reject malformed persisted records
  rather than silently defaulting fields.

### Task 3: Wire and verify the optional persistent API mode

- Add `database_path` injection to the app factory without changing existing
  route contracts or the default in-memory mode.
- Add an API restart integration scenario proving the same episode/status and
  command receipt remain available after app reconstruction.
- Run focused BDD/unit/integration tests, existing regressions, changed-scope
  `make check`, build, docs, lock, dependency, compile, credential, and
  clean-diff checks.
- Request independent Terra/Luna review for transaction boundaries, tenant
  predicates, idempotency races, redaction, and Decimal/UTC serialization.
- Fix every valid Critical, Important, or acceptance-blocking finding before
  creating a normal ready PR stacked on PR #18.

## Explicit deferrals

PostgreSQL driver/schema migrations, row-level database authorization, hosted
database operations, NATS JetStream/outbox publication, provider invoice
reconciliation, billing UI, retention/deletion workflows, and production backup
restore drills remain later M3/M7 work. This slice must label SQLite mode as
deterministic local demo mode.
