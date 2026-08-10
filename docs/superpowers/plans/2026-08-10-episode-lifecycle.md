# Episode lifecycle contract implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the tenant-scoped, idempotent episode lifecycle contract that the future asynchronous API and Temporal worker will consume, with a deterministic in-memory repository for offline verification.

**Architecture:** Keep lifecycle state, structured failures, immutable episode snapshots, and command validation in `poddown.episode_service`. Store only immutable records behind an `EpisodeRepository` protocol; use `InMemoryEpisodeRepository` as the deterministic local adapter. Reuse `poddown.intake.validate_markdown` and `poddown.content.source.snapshot_source` so source hashing and frontmatter behavior remain canonical. Provider dispatch, database persistence, object storage, HTTP transport, and workflow submission remain separate adapters.

**Tech Stack:** Python 3.12+, standard-library dataclasses/UUID/time/hashlib, the approved `uuid6==2025.0.1` package for UUIDv7 identifiers, pytest-bdd, pytest, and existing strict lint/type gates.

## Global constraints

- Implement only the offline lifecycle contract slice of `specs/004-episode-platform/spec.md`.
- Every command is tenant-scoped; a wrong-tenant lookup behaves as not-found and never leaks another tenant's existence.
- Duplicate idempotency keys return the original immutable episode only when the request fingerprint is identical; reuse with different content fails closed.
- Source bytes are decoded strictly, validated before snapshotting, hashed from the original bytes, and never stored in logs or error text.
- State transitions are explicit, monotonic, version-checked, and reject illegal publish-before-package/QA paths.
- Publishing requires an explicit authorization flag and never mutates the immutable package identity.
- The repository is an offline test adapter, not a claim of PostgreSQL, object-storage, API, or production deployment completion.
- Write pytest-bdd scenarios before production code and observe the expected RED collection/failure.

---

### Task 1: Define the lifecycle contract with failing BDD and unit tests

**Files:**

- Create: `tests/features/episode_lifecycle.feature`
- Create: `tests/bdd/test_episode_lifecycle.py`
- Create: `tests/unit/test_episode_service.py`

**Interfaces under test:**

- `EpisodeState`, `StructuredFailure`, `EpisodeCreateCommand`, `EpisodeRecord`.
- `EpisodeNotFound`, `IdempotencyConflict`, `InvalidEpisodeTransition`, `PublishAuthorizationError`, and `EpisodeValidationError`.
- `EpisodeApplicationService.create_episode`, `.get_episode`, `.transition`, and `.publish`.
- `InMemoryEpisodeRepository` implementing the tenant-scoped repository contract.

- [x] **Step 1: Write BDD scenarios first.** Cover exact source hashing/profile validation, same-request idempotent replay, conflicting idempotency reuse, cross-tenant not-found behavior, illegal state transitions, packaged/QA gating, explicit publish authorization, structured failure status, and optimistic version conflicts.

- [x] **Step 2: Add deterministic BDD bindings and fixtures.** Use local Markdown bytes, explicit profile names, a deterministic UUIDv7 factory, and a fixed UTC clock. Assert no provider calls, no source leakage, and exact returned state/evidence.

- [x] **Step 3: Add focused unit assertions.** Cover command validation, fingerprint identity, immutable records, repository tenant isolation, transition matrix, package checksum binding, failure payloads, and idempotency replay.

- [x] **Step 4: Observe RED.** Run:

```bash
uv run pytest -q tests/bdd/test_episode_lifecycle.py tests/unit/test_episode_service.py
```

Expected result: collection fails because `poddown.episode_service` does not exist yet.

### Task 2: Implement the offline lifecycle service and repository

**Files:**

- Create: `src/poddown/episode_service.py`
- Modify: `pyproject.toml` and `uv.lock` to add the dependency-advisor-approved `uuid6==2025.0.1`.

- [x] **Step 1: Implement immutable identifiers, commands, records, and failures.** Validate UUIDv7 IDs, tenant/project/key/profile identity, strict source bytes, source/profile snapshot evidence, exact request fingerprints, and structured failures without source text.

- [x] **Step 2: Implement tenant-scoped idempotent repository behavior.** Return the original record for identical replay, reject conflicting key reuse, hide cross-tenant records as not-found, and preserve immutable package/source checksums.

- [x] **Step 3: Implement explicit state transitions.** Enforce the approved transition matrix, optimistic version checks, QA/package evidence before `PACKAGED`, and explicit authorization before `PUBLISHED`; support terminal structured failure without regression.

- [x] **Step 4: Run focused green tests.** Run the BDD and unit suites from Task 1, then the existing intake/source regression suites.

### Task 3: Verify, document, review, and publish

**Files:**

- Create: `docs/verification/episode-lifecycle.md`
- Modify: `docs/planning-traceability.md`
- Modify: this plan

- [x] **Step 1: Run changed-scope `make check`.** Do not run `make check-full` or `make quality-gates` locally.

- [x] **Step 2: Run build, docs, lock, dependency, compile, schema, credential, and clean-diff checks.** Remove only exact generated coverage/cache files.

- [x] **Step 3: Request an independent Luna/Terra review.** Verify tenant isolation, idempotency, source fidelity, transition gates, and failure redaction; fix every valid Important or acceptance-blocking finding with a regression test.

- [x] **Step 4: Rebase onto the current M2 package-generation head and rerun verification.** The branch is ready for a normal PR with PR #14 as the direct parent. This slice intentionally does not claim PostgreSQL, S3, FastAPI, NATS, Temporal, or production API completion.

## Deferrals

SQLAlchemy/Alembic persistence, object storage, transactional outbox/NATS,
FastAPI transport/auth, Temporal submission, usage/cost event persistence,
publishing adapters, and the full asynchronous episode API remain subsequent
M3/M4 slices with their own BDD contracts.
