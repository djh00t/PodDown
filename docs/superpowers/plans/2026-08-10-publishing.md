# Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one immutable, verified episode package through replaceable offline-safe adapters with explicit authorization, deterministic outputs, resumable failure handling, and an immutable provenance-bound receipt.

**Architecture:** Add a focused `poddown.publishing` module containing immutable publication value objects, the `Publisher` port and orchestration service, and adapter implementations. The publisher validates package QA/checksum evidence before reading bytes, scopes storage access by tenant/project, records authorization/disclosure/provenance in the receipt, and uses an idempotency ledger to resume or compensate partial adapter work without duplicate feed entries. Filesystem and S3-compatible adapters share the existing `ObjectStore` contract; RSS is canonical XML; Transistor is an offline recorded-fixture contract adapter.

**Tech Stack:** Python 3.13, frozen dataclasses, existing `EpisodePackage`/`PackageArtifact` and `ObjectStore` contracts, pytest, pytest-bdd, XML parsing, recorded JSON fixtures, Ruff, Makefile gates.

## Global Constraints

- Only QA-passed immutable packages can publish.
- Repeated calls with one idempotency key return one external episode.
- Partial provider failure resumes or compensates without duplicate feed entries.
- Filesystem/S3 outputs exactly match package checksums.
- RSS is valid and deterministic; Transistor contract tests use recorded fixtures.
- Authorization, disclosure, and publication receipt are captured in provenance.
- Updating or deleting a publication is an explicit separately authorized action.
- Provider payloads do not cross the `Publisher` port.
- External providers remain offline by default; no live provider credentials or credits are used.
- Do not modify `src/poddown/cli.py`, MCP/skill files, workflow files, shared traceability, or other worktrees.

---

### Task 1: Define the publication contract and immutable receipt

**Files:**
- Create: `src/poddown/publishing.py`
- Test: `tests/unit/test_publishing_contracts.py`
- Create: `tests/features/publishing.feature`
- Test: `tests/bdd/test_publishing.py`

**Interfaces:**
- Consumes: `EpisodePackage`, `ObjectStore`, UUIDv7 tenant/project identifiers.
- Produces: frozen `PublicationTarget`, `PublicationAuthorization`, `DisclosurePolicy`, `PublicationReceipt`, `Publisher`, and typed publication errors.

- [ ] **Step 1: Write failing unit and BDD tests** for immutable receipt fields, package QA rejection, explicit authorization, separate update/delete authorization, and provider-payload isolation.
- [ ] **Step 2: Run the focused tests to verify RED** with missing `poddown.publishing` symbols.
- [ ] **Step 3: Implement the minimal immutable contract** with strict validation, stable error types, and a publisher protocol whose public method is `publish(package, target, authorization, idempotency_key)`.
- [ ] **Step 4: Run the focused unit/BDD tests to verify GREEN.**

### Task 2: Implement deterministic publication orchestration and idempotency

**Files:**
- Modify: `src/poddown/publishing.py`
- Test: `tests/unit/test_publishing_service.py`
- Test: `tests/integration/test_publishing.py`
- Modify: `tests/bdd/test_publishing.py`

**Interfaces:**
- Consumes: Task 1 contracts; adapter protocol with prepare/commit/compensate semantics.
- Produces: `PublishingService` that validates package state, binds tenant/project and provenance, returns one receipt per idempotency key, resumes safe retries, and compensates partial publication.

- [ ] **Step 1: Write failing tests** for exact package artifact bytes/checksums, replay identity, conflicting idempotency, partial failure retry, duplicate-feed prevention, authorization/disclosure provenance, and tenant isolation.
- [ ] **Step 2: Run focused tests to verify RED** because orchestration is not implemented.
- [ ] **Step 3: Implement the minimal orchestration and in-memory receipt ledger** with deterministic operation ordering and compensation for completed operations when a later operation fails.
- [ ] **Step 4: Run focused tests to verify GREEN** and refactor only while green.

### Task 3: Add filesystem and S3-compatible object publication adapters

**Files:**
- Modify: `src/poddown/publishing.py`
- Test: `tests/unit/test_publishing_storage.py`
- Test: `tests/integration/test_publishing_storage.py`

**Interfaces:**
- Consumes: existing `ObjectStore.read/put`, `ObjectRef`, package artifact checksums.
- Produces: `FilesystemPublicationAdapter` and `S3CompatiblePublicationAdapter` with identical checksum-bound behavior and no cloud SDK/network dependency.

- [ ] **Step 1: Write failing tests** for tenant/project-scoped reads, exact bytes, immutable content-addressed publication keys, replay, and rejection of mismatched/corrupt package content.
- [ ] **Step 2: Run focused storage tests to verify RED.**
- [ ] **Step 3: Implement adapters** as object-store-backed adapters; filesystem is an explicit local adapter and S3-compatible semantics are represented by a replaceable adapter over the same port, never by live network calls.
- [ ] **Step 4: Run focused storage tests to verify GREEN.**

### Task 4: Add deterministic RSS and recorded-fixture Transistor adapters

**Files:**
- Modify: `src/poddown/publishing.py`
- Create: `tests/fixtures/providers/transistor/episode-create.json`
- Create: `tests/fixtures/providers/transistor/episode-update.json`
- Test: `tests/unit/test_publishing_feeds.py`
- Test: `tests/contract/providers/test_transistor_publishing.py`
- Modify: `tests/bdd/test_publishing.py`

**Interfaces:**
- Consumes: publication metadata, disclosure policy, package checksums, recorded fixtures.
- Produces: deterministic `RssPublicationAdapter` and offline `RecordedTransistorAdapter`; no provider-specific payload crosses `Publisher`.

- [ ] **Step 1: Write failing tests** for canonical XML byte determinism, valid RSS required elements, disclosure handling, fixture replay, and no live provider access.
- [ ] **Step 2: Run feed/contract tests to verify RED.**
- [ ] **Step 3: Implement canonical RSS serialization and fixture-only Transistor responses** with stable external IDs and explicit unsupported-live failure.
- [ ] **Step 4: Run feed/contract/BDD tests to verify GREEN.**

### Task 5: Document verification and prepare review-ready commits

**Files:**
- Create: `docs/verification/publishing.md`
- Modify: `docs/superpowers/plans/2026-08-10-publishing.md`

- [ ] **Step 1: Record RED evidence** and the final focused validation commands/results.
- [ ] **Step 2: Document deferrals and residual risks** including production persistence, real S3 transport, live Transistor credentials, update/delete execution, and coordinator-owned files.
- [ ] **Step 3: Run the complete bounded acceptance commands**: focused BDD/unit/integration/contract tests, `make check`, `make build`, `make docs`, `uv lock --check`, `uv pip check`, `python -m compileall`, `git diff --check`, and credential scan.
- [ ] **Step 4: Review the diff for forbidden files and commit coherent publishing-specific changes without pushing.**

## Spec coverage self-review

- Contract and immutable receipt: Task 1.
- QA/package/checksum gates and idempotency: Tasks 1-3.
- Resumable/compensating partial failure and no duplicate feeds: Task 2.
- Filesystem/S3-compatible bytes: Task 3.
- Deterministic RSS and recorded Transistor: Task 4.
- Authorization, disclosure, provenance, and separate update/delete authorization: Tasks 1-2.
- Offline-only provider behavior and evidence: Tasks 4-5.
- Coordinator-only exclusions and handoff evidence: Task 5.

## Final correction evidence

- [x] Add scoped ObjectStore deletion and compensation regressions before implementation; RED showed the missing delete boundary and retained partial references.
- [x] Promote S3-compatible references only after all artifacts succeed; delete only newly-created references on failure and preserve reused objects.
- [x] Focused final publishing/object-storage validation: 50 passed.
- [x] Durable orphan garbage collection and cross-process transaction state remain explicitly deferred.
