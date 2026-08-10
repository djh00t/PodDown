# Offline episode lifecycle verification

## Scope

This slice adds the tenant-scoped lifecycle contract consumed by future API and
worker adapters. It validates source/profile input, snapshots exact source
hash evidence, assigns UUIDv7 episode identities, enforces idempotency by
tenant and request fingerprint, hides cross-tenant records as not-found, and
applies optimistic, monotonic state transitions through an in-memory adapter.

Packaging requires passing QA evidence and an immutable package SHA-256 bound
to the exact package bytes supplied at the boundary.
Publishing requires an explicit authorization decision. Status failures are
structured and redacted; source bytes and credentials are never retained in
the episode record or failure payload.

The in-memory repository is deterministic offline evidence. It is not a claim
that PostgreSQL, S3-compatible object storage, FastAPI transport, NATS, or
Temporal submission is complete.

## Acceptance evidence

The BDD contract in
[episode_lifecycle.feature](../../tests/features/episode_lifecycle.feature)
and [test_episode_lifecycle.py](../../tests/bdd/test_episode_lifecycle.py)
covers:

- exact source hashing and profile validation;
- same-request idempotent replay and conflicting-key rejection;
- tenant isolation and redacted status failures;
- illegal transitions, optimistic version conflicts, QA/package evidence;
- explicit publish authorization and package checksum preservation; and
- deterministic UUIDv7 and fixed-clock fixtures without provider calls.

Unit coverage is in
[test_episode_service.py](../../tests/unit/test_episode_service.py).

Focused lifecycle gate:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin -q tests/bdd/test_episode_lifecycle.py tests/unit/test_episode_service.py
38 passed
```

Existing intake/source/profile regressions also pass:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin -q tests/bdd/test_intake.py tests/unit/test_intake.py tests/unit/content/test_source.py tests/unit/content/test_profiles.py
65 passed
```

The direct dependency was approved by Dependency Advisor under conservative
Python policy: `uuid6==2025.0.1`, minimum release age 720 hours.

Changed-scope repository gate:

```text
make check
632 passed, 1 live-provider test deselected
coverage 86.45% (required 80.0%)
episode_service.py branch-aware coverage 85%
Ruff format/check and strict mypy passed
```

Additional checks passed: `make build`, `make docs`, `uv lock --check`,
`uv pip check`, Python compilation, `git diff --check`, and a changed-scope
credential pattern scan.

## Limitations and deferrals

The repository adapter is process-local and intentionally replaceable. SQL
models/migrations, object storage, outbox/events, FastAPI async endpoints,
auth/tenancy middleware, Temporal job submission, usage/cost persistence, and
production restart/recovery remain subsequent M3 slices.
