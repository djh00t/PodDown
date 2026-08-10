# M3 Episode API Transport Plan

> Execute this plan in the isolated `codex/m3-episode-api` worktree. The branch
> is based on PR16 (`codex/m3-episode-lifecycle`) and must be rebased onto the
> final PR16 head before publication.

## Objective

Deliver the first deterministic HTTP contract for Spec 004 over the offline
episode lifecycle service. The transport must expose versioned, non-blocking
episode commands with tenant/project context, idempotency, stable problem
details, and no credential or source-text leakage. It must be explicitly
labelled as an in-memory demo adapter; production persistence, authentication,
workflow dispatch, and metering remain subsequent slices.

## Scope and ownership

Owned by this branch:

- `src/poddown/api/__init__.py`
- `src/poddown/api/models.py`
- `src/poddown/api/runtime.py`
- `tests/features/episode_api.feature`
- `tests/bdd/test_episode_api.py`
- `tests/unit/test_api_models.py`
- `tests/integration/test_episode_api.py`
- `docs/verification/episode-api.md`
- this plan

Shared files require explicit coordinator review before editing:

- `pyproject.toml` and `uv.lock` for the dependency-advisor-approved FastAPI
  pin and supported `httpx2` test client dependency
- `docs/planning-traceability.md`
- `src/poddown/episode_service.py` (prefer an adapter over modifying the
  lifecycle contract)

## Contract decisions

- Dependency advisor selected `fastapi==0.139.0` and `httpx2==2.5.0`, the
  supported Starlette TestClient dependency; both choices are recorded in the
  branch lockfile before implementation.
- Local/demo tenant and project context is supplied by required
  `X-Tenant-ID` and `X-Project-ID` UUID headers. This is an explicit adapter
  boundary, not production authentication.
- Mutating endpoints require `Idempotency-Key`; missing or malformed context
  returns stable `application/problem+json` without invoking the service.
- `POST /v1/episodes` accepts a JSON body with UTF-8 `source` and registered
  `profile`, returning `202` with the episode summary.
- `GET /v1/episodes/{id}` and `/status` are tenant-scoped and never expose
  source bytes or raw parser/provider details.
- `POST /v1/episodes/{id}/render` and `/publish` call injected command ports
  and return `202` when their state/authorization gates pass; the default demo
  dispatcher records a resumable command receipt without provider/network/
  storage work.
- Status failures use an allowlisted projection (`code`, `stage`, `status`,
  `retriable`) and never return raw failure messages or details.
- Render and publish command idempotency is keyed by tenant, episode, command,
  and idempotency key. Conflicting reuse returns `409`.
- `publish` additionally requires an explicit `X-Publish-Authorization: true`
  demo header; no implicit approval is accepted.

## Acceptance behaviors

1. Valid create returns HTTP 202, UUIDv7 episode ID, current version/state,
   source checksum/byte count, and a resumable command/job receipt.
2. Identical create replay returns the original resource; conflicting replay
   returns HTTP 409 without a second resource.
3. Missing/invalid tenant, project, idempotency, profile, or UTF-8 source
   returns stable problem details and does not dispatch a command.
4. Cross-tenant get/status/render/publish returns 404 without leaking whether
   the episode exists.
5. Status exposes stage, version, and structured failure but never source text,
   credentials, or raw exception details.
6. Render is non-blocking and idempotent; publish requires explicit demo
   authorization, remains gated before packaging, and is non-blocking.
7. No API test performs provider, network, object-storage, database, or live
   credential work.

## TDD execution order

### Task 1: Write executable BDD and focused tests first

- [x] Add `tests/features/episode_api.feature` for create/status/get, idempotent
  replay/conflict, context validation, tenant isolation, render/publish
  command receipts, stable failures, and provider-free execution.
- [x] Add step bindings in `tests/bdd/test_episode_api.py`.
- [x] Add model and pure helper tests in `tests/unit/test_api_models.py`.
- [x] Add ASGI integration tests in `tests/integration/test_episode_api.py`.
- [x] Observe collection fail because the API module and FastAPI dependency do not
  exist yet; preserve that RED evidence in the verification note.

### Task 2: Implement the smallest complete transport

- [x] Use the dependency advisor before adding FastAPI/test-client packages.
- [x] Implement immutable request/response models and stable problem-detail
  conversion without returning source text or secrets.
- [x] Implement explicit tenant/project context extraction and UUID validation.
- [x] Implement an injected in-memory command dispatcher with idempotent receipts.
- [x] Wire the five Spec 004 routes to `EpisodeApplicationService` and the ports.
- [x] Project the status failure allowlist and test failed-episode redaction.
- [x] Keep all external integrations behind interfaces; default tests remain
  deterministic and offline.

### Task 3: Verify, review, and publish

- [x] Run focused BDD/unit/integration tests and intake/lifecycle regressions.
- [x] Run changed-scope `make check`, build, docs, lock, dependency, compile,
  schema, credential, and clean-diff checks. Do not run `make check-full` or
  `make quality-gates` locally.
- [x] Request an independent Terra/Luna review focused on tenant isolation,
  idempotency, status redaction, authorization, and non-blocking dispatch.
- [x] Fix every valid Important or acceptance-blocking finding with a regression
  test, rebase onto the final PR16 head, and rerun the gate.
- [ ] Push and create a normal ready PR directly stacked on PR16.

## Explicit deferrals

PostgreSQL/Alembic persistence and row-level tenant isolation, S3-compatible
object storage, transactional outbox/NATS, real auth, Temporal workflow
submission, provider dispatch, usage/cost persistence, restart recovery, and
short-lived artifact URLs remain subsequent implementation slices.
