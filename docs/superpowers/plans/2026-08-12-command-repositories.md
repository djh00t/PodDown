# Command Repository Seams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add narrow durable command/job and publication-approval repositories over the existing production schema contracts.

**Architecture:** `command_repository.py` persists a stable render-job workflow identity and guarded state transitions. `publication_approval_repository.py` persists and atomically consumes scoped approvals through the existing `publications` relationship. Both receive a DB-API connection factory, so SQLite-compatible deterministic tests exercise the repositories without a PostgreSQL driver.

**Tech Stack:** Python 3.12, DB-API 2.0-shaped connections, SQLite test doubles, pytest-bdd, Ruff, mypy.

## Global Constraints

- Create only the two production repository modules; do not edit API, MCP, provider, or migration modules.
- Preserve tenant/project/episode predicates for every read or write.
- Treat replay as idempotent only for identical immutable identity; reject conflicts safely.
- Consume approvals once, before their expiry, in the caller's full scope.
- Do not introduce PostgreSQL driver setup or live database integration.

---

### Task 1: Command/job repository

**Files:**
- Create: `src/poddown/command_repository.py`
- Test: `tests/unit/test_command_repository.py`
- Test: `tests/features/command_repositories.feature`
- Test: `tests/bdd/test_command_repositories.py`

**Interfaces:**
- Produces: `CommandJobRepository.record_or_replay(...)`, `CommandJobRepository.transition(...)`, and typed safe errors.
- Uses: injected DB-API connection factory and existing `render_jobs` table identity.

- [ ] Write failing restart/replay/conflict/tenant-isolation scenarios and unit tests.
- [ ] Run the focused tests and observe missing-module failures.
- [ ] Implement only stable job/workflow persistence and valid state transitions.
- [ ] Re-run the focused command tests.

### Task 2: Publication approval repository

**Files:**
- Create: `src/poddown/publication_approval_repository.py`
- Test: `tests/unit/test_publication_approval_repository.py`
- Test: `tests/features/command_repositories.feature`
- Test: `tests/bdd/test_command_repositories.py`

**Interfaces:**
- Produces: `PublicationApprovalRepository.record(...)` and `PublicationApprovalRepository.consume(...)` with typed safe errors.
- Uses: injected DB-API connection factory and existing `publication_approvals` joined to `publications`.

- [ ] Write failing one-time scoped consumption scenarios and unit tests.
- [ ] Run the focused tests and observe missing-module failures.
- [ ] Implement record and atomic one-time consume behavior.
- [ ] Re-run all focused repository tests.

### Task 3: Validation and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-08-12-command-repositories.md`

- [ ] Run focused tests, Ruff format/check, strict mypy, `make check`, and `git diff --check`.
- [ ] Commit all D11 files in one Conventional Commit without pushing.
