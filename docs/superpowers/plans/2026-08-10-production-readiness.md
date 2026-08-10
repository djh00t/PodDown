# M7 Production Readiness Contract Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, offline-verifiable production-readiness contract for Compose topology, health degradation, redacted operational telemetry, and operational evidence without claiming live infrastructure readiness.

**Architecture:** Keep runtime topology declarative in a versioned Compose file and validate it with local schema/contract tests. Add a small provider-free operational contract module for health snapshots, metrics, and redacted events; expose health through the existing FastAPI factory only if the integration remains additive. Document security, retention, backup/restore, reproducibility, and explicit production deferrals in one verification record.

**Tech Stack:** Python 3.12+, dataclasses, FastAPI, PyYAML, pytest/pytest-bdd, Docker Compose YAML (not executed by default).

## Global Constraints

- Compose includes PostgreSQL, Temporal, NATS JetStream, S3-compatible MinIO, API, and worker; Kubernetes is excluded.
- Default tests require no Docker daemon, live services, network calls, provider credentials, or database credentials.
- Operational events and metrics contain tenant-safe identifiers only; source text, scripts, audio bytes/paths, credential values, tokens, and raw provider payloads are rejected or redacted.
- Health distinguishes liveness, ready, and dependency-degraded states deterministically.
- This slice documents but does not claim hosted migrations, real backup restore, provider outage/budget/load testing, signing/SBOM, or deployment credentials.
- Do not modify MCP/skill, publishing, CLI, Signal fixture, or audio modules.
- Run `make check`, not `make check-full` or `make quality-gates`, for local changed-scope validation.

---

### Task 1: Add BDD contract scenarios and focused RED tests

**Files:**
- Create: `tests/features/production_readiness.feature`
- Create: `tests/bdd/test_production_readiness.py`
- Create: `tests/unit/test_production_contracts.py`
- Create: `tests/unit/test_compose_contract.py`
- Create: `tests/integration/test_health_boundary.py`

**Interfaces:**
- Tests will import `poddown.production_readiness` contracts and `poddown.api.create_app`.
- Tests will load `compose.yaml` and assert the required service names, healthchecks, dependency conditions, and no Kubernetes manifests.

- [ ] Write scenarios for deterministic liveness/readiness/dependency degradation, tenant-safe event redaction, Compose topology validation, and explicit deferred live evidence.
- [ ] Run the focused tests and record the expected RED failure because `poddown.production_readiness` and the Compose contract are absent.

### Task 2: Implement health and operational contracts

**Files:**
- Create: `src/poddown/production_readiness.py`
- Modify: `src/poddown/api/__init__.py` only to add `/health/live`, `/health/ready`, and `/health/dependencies` if the factory integration is additive.

**Interfaces:**
- `DependencyState`: literal states `healthy`, `degraded`, `unavailable`.
- `HealthSnapshot`: immutable service/overall/dependencies response with deterministic JSON conversion.
- `HealthEvaluator.evaluate(liveness: bool, dependencies: Mapping[str, DependencyState]) -> HealthSnapshot`.
- `OperationalEvent.create(event_name, tenant_id, project_id, correlation_id, attributes) -> OperationalEvent`.
- `MetricSample.create(name, value, tenant_id, project_id, labels) -> MetricSample`.
- Redaction rejects keys matching source/script/audio/credential/token/provider-payload names and replaces unsafe values with `[REDACTED]`.

- [ ] Implement the smallest immutable contracts that make the RED tests pass.
- [ ] Add health route responses without touching episode, publishing, CLI, MCP, Signal, or audio behavior.
- [ ] Keep dependency checks injected and offline; no probes or network calls in the default factory.

### Task 3: Add the versioned local Compose contract and deterministic fixtures

**Files:**
- Create: `compose.yaml`
- Create: `tests/fixtures/production/compose-contract.yaml`
- Modify: `pyproject.toml` only if a currently available dependency is insufficient; do not add runtime infrastructure clients.

**Interfaces:**
- Compose project name/version and service keys are stable and testable.
- Services: `postgres`, `temporal`, `nats`, `minio`, `api`, `worker`.
- API and worker depend on health-gated infrastructure services; no credentials are committed, only environment variable names and local development placeholders that are explicitly non-secret.

- [ ] Add the minimal Compose topology with healthchecks and named local volumes.
- [ ] Add schema/contract tests for service names, ports, healthchecks, dependency conditions, JetStream command, MinIO S3 endpoint, and absence of Kubernetes claims.
- [ ] Run focused Compose tests and confirm deterministic parsing.

### Task 4: Document operational controls, deferrals, and evidence

**Files:**
- Create: `docs/verification/production-readiness.md`
- Create: `docs/operations/security-retention-backup.md`

- [ ] Document tenant-safe logging/metrics, secret injection/rotation, retention classes, deletion/audit boundaries, backup/restore runbook expectations, reproducibility, and release evidence.
- [ ] Record exact offline commands and results only after they are run.
- [ ] Explicitly mark hosted migrations, real restore, outage/budget/load testing, signing/SBOM, deployment credentials, and clean Compose E2E as deferred or unrun.

### Task 5: Validate and hand off

- [ ] Run focused BDD/unit/integration/Compose tests.
- [ ] Run changed-scope `make check` only.
- [ ] Run `make build`, `make docs`, `uv lock --check`, `uv pip check`, `uv run python -m compileall -q src tests`, `git diff --check`, and a credential scan over changed files.
- [ ] Inspect `git status`, ensure no forbidden files changed, and record worktree state.
- [ ] Create one local Conventional Commit only after all required evidence is fresh; do not push, create a PR, merge, or approve.

## Self-review

- Spec runtime is covered by Task 3 and health behavior by Tasks 1-2.
- Operations/security/retention are covered by Tasks 2 and 4.
- Acceptance deferrals are explicit in Global Constraints and Task 4; no live infrastructure claim is made.
- No task owns forbidden publishing, CLI, MCP/skill, Signal, or audio paths.
- Tests precede production code in Task 1 and RED is explicitly captured.
