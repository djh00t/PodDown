# Source-Bound Production Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the API-to-Temporal production path preparation-driven for configured non-fixture episodes while retaining explicit reference-demo behavior.

**Architecture:** A production command may carry only source/preparation authority. Temporal performs preparation first, then accepts the JSON render snapshot projected from the validated preparation result. Runtime composition supplies a worker-owned content request factory and explicit render binding; absent configuration remains fail-closed.

**Tech Stack:** Python 3.13 in the current environment, Python 3.12+ target, Pydantic, Temporal Python SDK, pytest/pytest-bdd, Ruff, strict mypy, existing PodDown content/audio/provider contracts.

## Global Constraints

- Preserve source bytes, source anchors, canonical claims, pronunciation evidence, and critical-token identity.
- Keep `deterministic-local`, `host-local`, and `live-provider` visibly distinct.
- Live provider execution requires explicit credential, consent, route, and cost configuration; no silent local fallback.
- Providers remain ports/adapters and secrets never enter records or Temporal payloads.
- Use BDD acceptance coverage before implementation and TDD red/green/refactor.
- Do not commit, push, merge, approve, deploy, or publish without explicit authority.
- Keep the reference fixture path working and do not delete preserved historical package/phase directories.

### Task 1: Extend immutable snapshot and preparation wire contracts

**Files:**
- Modify: `src/poddown/workflow_snapshots.py`
- Modify: `src/poddown/audio/production_workflow.py`
- Modify: `src/poddown/audio/prepare_activity.py`
- Test: `tests/unit/test_workflow_snapshots.py`
- Test: `tests/unit/audio/test_prepare_activity.py`
- Test: `tests/bdd/test_production_workflow.py`
- Test: `tests/features/production_workflow.feature`

**Interfaces:**
- `EpisodeWorkflowSnapshot.workflow_input` becomes optional only when a valid `production_input_json` is present.
- `EpisodeWorkflowSnapshot.to_payload()` omits `workflow_input` for source-only production commands.
- `PrepareContentActivityInput` accepts optional production context fields `episode_version_id`, `execution_mode`, and `max_attempts`; ordinary preparation callers remain valid.
- `PrepareContentActivityResult.render_workflow_input_json` is an optional JSON-safe field.
- `render_workflow_input_for()` remains strict for callers requiring a prebuilt snapshot; a new `render_workflow_input_from_preparation()` validates the activity projection.

- [x] **Step 1: Write failing BDD and unit tests** for source-only snapshots, production context round-trips, and preparation results carrying render JSON.
- [x] **Step 2: Run the focused tests and verify they fail for the missing contract behavior.**
- [x] **Step 3: Implement the smallest contract changes and strict validation.**
- [x] **Step 4: Run the focused tests and existing snapshot/preparation tests.**

### Task 2: Make the production workflow preparation-driven

**Files:**
- Modify: `src/poddown/audio/production_workflow.py`
- Modify: `src/poddown/audio/prepare_activity.py`
- Modify: `src/poddown/audio/production_activities.py`
- Modify: `src/poddown/workflow_snapshots.py`
- Test: `tests/bdd/test_production_workflow.py`
- Test: `tests/bdd/test_production_activities.py`
- Test: `tests/unit/audio/test_workflow_contracts.py`

**Interfaces:**
- Add a worker-owned render-snapshot builder using `ContentPreparationResult` and `WorkflowRenderBinding`.
- `build_production_activities()` accepts that builder and projects its result into the preparation activity output.
- `EpisodeProductionWorkflow.run()` executes preparation before resolving a deferred render input, verifies all identity fields, then executes the existing render/master/QA/package/publish stages.
- Existing prebuilt render snapshots bypass only the new projection step and retain the same validation.

- [x] **Step 1: Add a failing test where a source-only production input receives render JSON from preparation and reaches the render child.**
- [x] **Step 2: Add failing tests for changed source/provider/consent evidence and missing render projection.**
- [x] **Step 3: Implement the preparation-to-render projection and workflow handoff.**
- [x] **Step 4: Run production workflow/activity focused tests and refactor only after green.**

### Task 3: Add explicit configured content runtime composition

**Files:**
- Create: `src/poddown/content/runtime.py`
- Modify: `src/poddown/runtime.py`
- Modify: `src/poddown/runtime_snapshots.py`
- Modify: `src/poddown/providers/runtime.py`
- Test: `tests/bdd/test_runtime_composition.py`
- Test: `tests/bdd/test_runtime_snapshot_factory.py`
- Test: `tests/unit/test_runtime_entrypoints.py`
- Test: `tests/unit/test_workflow_snapshots.py`

**Interfaces:**
- Add an explicit configured content registry that resolves profile YAML, voice/consent metadata, pronunciation layers, and treatment policy from a configured root.
- The registry builds a `ContentPreparationRequest` for a source; live mode injects the configured `LiveAdaptationService`, while local modes require source-bound configured adaptation and never call live providers.
- `runtime_workflow_snapshot_factory()` selects the reference factory only when `PODDOWN_WORKFLOW_FIXTURE_ROOT` is present; otherwise it selects the configured production factory only when its content root and route configuration are complete, or returns `None`/fails closed.
- Worker composition registers generic production activities with the same explicit content registry and binding used by the API snapshot authority.

- [x] **Step 1: Add BDD scenarios for complete generic configuration, incomplete configuration, and no local fallback.**
- [x] **Step 2: Run the new scenarios and verify the missing runtime factory/configuration behavior fails.**
- [x] **Step 3: Implement the registry and runtime selection with secret-free configuration parsing.**
- [x] **Step 4: Run runtime composition, provider policy, and snapshot tests.**

### Task 4: Integrate API command envelopes and documentation

**Files:**
- Modify: `src/poddown/audio/workflow.py`
- Modify: `src/poddown/api/__init__.py`
- Modify: `docs/verification/temporal-publication.md`
- Modify: `docs/verification/authenticated-api.md`
- Modify: `docs/feature-status-matrix.md`
- Test: `tests/bdd/test_api_temporal_snapshot.py`
- Test: `tests/integration/test_episode_api.py`
- Test: `tests/unit/test_api_snapshot_requirement.py`

**Interfaces:**
- Command envelope validation accepts a source-only production payload and requires a render snapshot for legacy render-only payloads.
- API replay compares the complete source/preparation authority and preserves the existing receipt identity semantics.
- Documentation distinguishes source-only preparation authority from live/hosted readiness evidence.

- [x] **Step 1: Add failing API/Temporal envelope tests for source-only production dispatch and replay mismatch.**
- [x] **Step 2: Implement the envelope and replay changes.**
- [x] **Step 3: Run API/snapshot integration tests and update documentation with exact local evidence.**

### Task 5: Verification and handoff

**Files:**
- Modify: `docs/feature-status-matrix.md`
- Modify: `docs/verification/production-readiness.md`

- [x] **Step 1: Run focused BDD/unit/integration tests for all changed modules.**
- [x] **Step 2: Run Ruff format/check, strict mypy, `git diff --check`, build/lock/pip checks where available.**
- [x] **Step 3: Run the broad repository gate without converting Temporal environment failures into application passes.**
- [x] **Step 4: Record exact evidence and remaining hosted/live gaps in the matrix.**

## Local completion record

The reconciliation worktree completed this plan locally on 2026-08-15. The
latest non-live repository run passed 1560 tests with one live-provider test
deselected and 80.22% branch coverage; the API-to-Temporal integration set
included a real local Temporal server/worker run. Ruff, strict mypy,
compileall, `git diff --check`, and the package build passed. `uv lock --check`
and `uv pip check` were rejected by the execution environment, so no lock/pip
pass is claimed. Hosted PostgreSQL/Temporal, live-provider, external
publication, authenticated UAT, and deployment evidence remain deferred.
