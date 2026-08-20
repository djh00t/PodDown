# PostgreSQL Usage Evidence Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the configured production worker persist provider evidence and usage in PostgreSQL instead of silently using SQLite when `PODDOWN_POSTGRES_DSN` is present.

**Architecture:** Extend the existing transaction-bound `PostgresEvidenceLedger` with usage-only read/write operations, then add a short-lived-connection `PostgresUsageLedger` adapter implementing the existing `UsageLedger` port. Runtime composition selects PostgreSQL for the DSN-backed worker and preserves SQLite for explicit local SQLite operation.

**Tech Stack:** Python 3.12+, Psycopg-compatible DB-API ports, Pydantic-adjacent immutable contracts, pytest/pytest-bdd, Ruff, strict mypy.

## Global Constraints

- Provider evidence and usage must be committed in one PostgreSQL transaction.
- Every protected query must set the transaction-local tenant context before reading or writing.
- Replaying the same provider request must return identical evidence; conflicting data must fail closed.
- Secrets and DSN values must never enter records, serialized workflow payloads, or errors.
- SQLite remains the explicit local adapter; it is not selected for DSN-backed production workers.
- No live provider call, deployment, external publication, commit, or push is part of this slice.

---

### Task 1: Specify the PostgreSQL usage adapter behavior

**Files:**
- Create: `tests/unit/test_postgres_usage.py`
- Modify: `tests/features/postgres_ledger.feature`
- Modify: `tests/bdd/test_postgres_ledger.py`

**Interfaces:**
- Consumes: `UsageEvent`, `ProviderEvidence`, and the existing transaction-bound `PostgresEvidenceLedger`.
- Produces: failing tests for `PostgresUsageLedger.record`, `record_evidence`, `get`, `get_evidence`, and `list_for_job`.

- [x] **Step 1: Add a focused failing test for atomic evidence/usage replay.**

  Use a fake connection with a transaction context and the existing SQL-aware cursor. Assert that the adapter returns the same pair on replay and that a conflicting request raises `PostgresLedgerConflict`.

- [x] **Step 2: Add a failing test for tenant-scoped reads and stable job ordering.**

  Seed two usage rows and assert `list_for_job(tenant_id, job_id)` returns only that tenant/job in `(created_at, provider_request_id)` order; assert `get` raises `UsageNotFound` for an absent request.

- [x] **Step 3: Run the focused tests and verify they fail for the missing adapter.**

  Run:

  ```bash
  PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    .venv/bin/pytest -q tests/unit/test_postgres_usage.py tests/bdd/test_postgres_ledger.py
  ```

  Expected result: collection or assertion failure because `PostgresUsageLedger` and its usage operations do not yet exist.

### Task 2: Implement transaction-bound PostgreSQL usage operations

**Files:**
- Modify: `src/poddown/postgres_ledger.py`
- Modify: `tests/unit/test_postgres_usage.py`

**Interfaces:**
- Consumes: `ConnectionFactory` returning a connection with `cursor()`, `transaction()`, and optional `close()`.
- Produces: `PostgresUsageLedger` implementing `UsageLedger` and preserving the existing evidence ledger as the transaction primitive.

- [x] **Step 1: Add minimal public usage methods to `PostgresEvidenceLedger`.**

  Implement transaction-bound `record_usage`, `get_usage`, and `list_usage` helpers using the existing `_get_usage`/`_usage_event` validation and parameterized SQL. Do not add commits inside these helpers.

- [x] **Step 2: Add `PostgresUsageLedger` with one connection/transaction per operation.**

  Implement `record`, `record_evidence`, `get`, `get_evidence`, and `list_for_job`. Each operation must open a connection, enter its transaction context, call the transaction-bound ledger, and close the connection in `finally`. `record_evidence` must call the existing pair operation so evidence and usage commit together.

- [x] **Step 3: Run the focused tests and verify they pass.**

  Run the command from Task 1. Expected result: all new adapter tests and existing PostgreSQL ledger BDD tests pass.

### Task 3: Select the durable ledger in worker runtime composition

**Files:**
- Modify: `src/poddown/runtime.py`
- Modify: `tests/unit/test_runtime_entrypoints.py`
- Modify: `tests/features/runtime_composition.feature`
- Modify: `tests/bdd/test_runtime_composition.py`

**Interfaces:**
- Consumes: `PODDOWN_POSTGRES_DSN`, `postgres_connection_factory`, and `PostgresUsageLedger`.
- Produces: explicit runtime selection: PostgreSQL for DSN-backed worker evidence, SQLite only when the worker is intentionally operating without a PostgreSQL DSN in local contract mode.

- [x] **Step 1: Add a failing runtime-composition test.**

  Configure live-provider worker content plus a PostgreSQL DSN, patch the connection factory, and assert the provider evidence recorder is backed by `PostgresUsageLedger`; assert a missing DSN remains a configuration error for live production mode.

- [x] **Step 2: Implement a small runtime ledger factory.**

  Add a private helper that returns `PostgresUsageLedger(postgres_connection_factory(dsn))` when `PODDOWN_POSTGRES_DSN` is non-empty, otherwise returns `SQLiteUsageLedger` for the explicitly supported local path. Use the helper in both durable render and complete production stage composition.

- [x] **Step 3: Run focused runtime tests.**

  ```bash
  PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    .venv/bin/pytest -q tests/unit/test_runtime_entrypoints.py tests/bdd/test_runtime_composition.py
  ```

  Expected result: runtime selection and existing composition tests pass.

### Task 4: Document and verify the closure boundary

**Files:**
- Modify: `docs/feature-status-matrix.md`
- Modify: `docs/verification/production-data-plane.md`
- Modify: `docs/verification/runtime-composition.md`

- [x] **Step 1: State the selection rule and evidence boundary.**

  Document that DSN-backed worker provider evidence uses PostgreSQL transactions, while local SQLite remains an explicit contract adapter; do not claim a live PostgreSQL or Compose run.

- [x] **Step 2: Run formatting, type, diff, and focused tests.**

  ```bash
  .venv/bin/ruff format --check src tests
  .venv/bin/ruff check src tests
  .venv/bin/python -m mypy
  .venv/bin/python -m compileall -q src tests
  git diff --check
  ```

- [x] **Step 3: Inspect the effective diff and preserve all unrelated historical reconciliation work.**

  Confirm only the adapter, its tests, runtime selection, and evidence documentation were added or modified by this slice; leave the worktree uncommitted.
