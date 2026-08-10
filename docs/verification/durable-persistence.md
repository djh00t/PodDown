# Durable persistence and metering verification

## Scope

This slice adds restart-safe SQLite adapters behind the episode repository and
API command-dispatch ports, plus an append-only tenant-scoped usage/cost ledger.
The optional API mode is enabled with `create_app(database_path=...)`; the
default app remains the deterministic in-memory adapter.

Persisted records contain UUIDv7 tenant/project/job identifiers, checksums,
bounded metadata, lifecycle state, optimistic versions, redacted failures,
structured units, currency, and Decimal cost strings. Source bytes, audio,
credentials, and provider secrets are never stored by these adapters.

SQLite is a local deterministic demo adapter. PostgreSQL/Alembic migrations,
database row-level authorization, NATS/outbox delivery, hosted operations, and
provider-invoice reconciliation remain explicitly deferred.

## TDD evidence

The contract tests were written before the production module existed:

```text
uv run pytest -q tests/unit/test_persistence.py \
  tests/integration/test_durable_persistence.py \
  tests/bdd/test_durable_persistence.py
ModuleNotFoundError: No module named 'poddown.persistence'
```

After implementation, the same focused selection passed **25 tests**.

## Verification commands and results

```bash
make check
```

Result: **730 passed, 1 live-provider test deselected**, with no resource
warnings. Branch-aware total coverage was **86.64%** against the repository's
80% threshold; `poddown.persistence` was **89%**.

The suite includes BDD restart/provider-free scenarios, concurrent idempotent
episode creation, cross-tenant isolation, stale-version rejection, durable API
command receipts, malformed-row fail-closed behavior, exact Decimal cost
round-trips, and usage-request conflict protection.

Additional checks passed:

```bash
make build
make docs
uv lock --check
uv pip check
uv run python -m compileall -q src tests
git diff --check
```

The credential scan found no matches. All tests use temporary local SQLite files;
no database server, provider, network, credential, or cloud spend is invoked.
