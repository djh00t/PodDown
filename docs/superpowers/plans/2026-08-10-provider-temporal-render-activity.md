# Provider-Bound Temporal Render Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the existing immutable durable-render service to the Temporal render activity boundary so a deterministic local episode performs real artifact persistence, quality evaluation, replay-safe retries, and exactly-once local cost evidence.

**Architecture:** The workflow remains deterministic and passes an immutable episode snapshot plus a stable activity key to a provider-neutral activity factory. The factory reconstructs and validates the segment request, delegates rights/capability/idempotency work to `DurableRenderService`, reads the verified artifact, runs an injected quality evaluator, and returns the existing `CandidateQuality` contract. Provider calls stay behind `AudioRenderer`; the default evaluator is explicitly deterministic-local and uses the expected spoken text as its transcript until the transcription milestone supplies a real transcript port.

**Tech Stack:** Python 3.12, Temporal Python SDK 1.30, pytest-bdd, pytest, filesystem content-addressed artifacts, immutable JSON contracts, deterministic local WAV renderer.

## Global Constraints

- Provider dispatch requires matching, valid `VoiceConsent` and a provider that advertises every required capability.
- Activity identity must derive from episode snapshot, stage, segment, attempt, and take; retries must not overwrite accepted candidates or immutable artifacts.
- Each attempt may execute at most three takes, and hard gates precede soft-score selection.
- Malformed audio, invalid consent, contract mismatches, and unsupported provider behavior fail closed and are non-retryable Temporal application errors.
- Offline deterministic fixtures remain the default; no live provider credentials or provider spend are introduced by this milestone.
- Local verification uses `make check`; `make check-full` and `make quality-gates` remain post-merge CI responsibilities.

---

## File and Ownership Map

- Create `tests/features/provider_temporal_render.feature` for executable activity-boundary acceptance behavior.
- Create `tests/bdd/test_provider_temporal_render.py` for the BDD context, local stores, deterministic renderer, and Temporal worker fixtures.
- Create `tests/unit/audio/test_activities.py` for payload, error-mapping, quality-evaluator, and replay-unit coverage.
- Create `tests/unit/audio/test_local.py` for the deterministic renderer's PCM
  no-clipping regression exposed by the real artifact diagnostics.
- Create `src/poddown/audio/activities.py` for the provider-neutral Temporal activity factory and deterministic-local quality evaluator.
- Modify `src/poddown/audio/workflow.py` to include the immutable episode snapshot in each activity payload so the activity can authenticate its stable key and segment snapshot.
- Modify `src/poddown/audio/__init__.py` to export the activity factory and evaluator contract.
- Modify `tests/unit/audio/test_workflow.py` only if the existing workflow payload assertions require the new `episode` field.
- Modify `tests/integration/test_temporal_orchestration.py` only for shared worker-registration coverage; the durable persistence integration has its own test module.
- Create `tests/integration/test_temporal_durable_render_activity.py` for real Temporal + filesystem-store + deterministic-renderer verification.
- Create `docs/verification/provider-temporal-render-activity.md` for commands, evidence, deterministic-mode limitations, and the exact deferred boundaries.
- Modify `docs/planning-traceability.md` to mark provider-bound local activity wiring and local cost/idempotency replay evidence complete while retaining transcription, mastering, packaging, and publication as pending.

## Interfaces

The activity module will expose these exact public contracts:

```python
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

QualityEvaluator: TypeAlias = Callable[
    [RenderRequest, bytes, AudioDiagnostics, tuple[str, ...]], CandidateQuality
]
ActivityHandler: TypeAlias = Callable[
    [dict[str, Any]], Awaitable[dict[str, Any]]
]

def deterministic_quality_evaluator(
    request: RenderRequest,
    audio_bytes: bytes,
    diagnostics: AudioDiagnostics,
    critical_tokens: tuple[str, ...],
) -> CandidateQuality: ...

def build_durable_render_activity(
    service: DurableRenderService,
    renderer: AudioRenderer,
    artifacts: FilesystemArtifactStore,
    *,
    quality_evaluator: QualityEvaluator | None = None,
) -> ActivityHandler: ...
```

The handler accepts the existing workflow payload plus the authenticated snapshot:

```python
{
    "episode": episode_input.to_dict(),
    "segment": segment.to_dict(),
    "attempt": attempt,
    "take": take,
    "activity_key": activity_key_for(
        episode_input,
        "render",
        segment.segment_id,
        attempt=attempt,
        take=take,
    ),
}
```

It returns `CandidateQuality.to_dict()` and never returns provider credentials, raw audio bytes, or an unverified artifact path.

### Task 1: Define the activity-boundary behavior before production code

**Files:**
- Create: `tests/features/provider_temporal_render.feature`
- Create: `tests/bdd/test_provider_temporal_render.py`
- Modify: `src/poddown/audio/workflow.py`
- Test: `tests/unit/audio/test_workflow.py` if payload serialization needs an assertion

**Interfaces:**
- Consumes: `EpisodeWorkflowInput`, `SegmentWorkflowInput`, `activity_key_for`, and the existing render activity name.
- Produces: a red acceptance contract for real durable activity execution, retry-after-persist replay, rights rejection, and stable payload authentication.

- [ ] **Step 1: Write the failing Gherkin scenarios**

Add scenarios with these exact behaviors:

```gherkin
Feature: durable provider-bound Temporal render activity

  Scenario: durable activity persists one candidate and one cost event
    Given a consented deterministic local render segment
    When the provider-bound Temporal activity runs
    Then the renderer is called once and the returned candidate passes local QA
    And exactly one immutable artifact and one cost event are persisted

  Scenario: retry after persistence replays without duplicate provider usage
    Given a consented deterministic local render segment
    And the first activity invocation fails after durable persistence
    When the Temporal workflow retries the same activity key
    Then the workflow completes
    And the renderer is called once for that candidate
    And the cost event count remains one

  Scenario: invalid consent fails before provider dispatch
    Given a deterministic local render segment without matching provider consent
    When the provider-bound Temporal activity runs
    Then the activity fails with a non-retryable rights error
    And the renderer is never called
```

- [ ] **Step 2: Implement only the BDD context and explicit RED imports**

Build the segment with `RenderRequest`, `VoiceConsent`, `SegmentWorkflowInput`, `EpisodeWorkflowInput`, `FilesystemArtifactStore`, `FilesystemRenderRecordStore`, `DurableRenderService`, and `DeterministicLocalRenderer`. Import `build_durable_render_activity` through the public package boundary so the test fails with the missing activity contract before production implementation exists.

- [ ] **Step 3: Run the BDD file and observe the expected failure**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/bdd/test_provider_temporal_render.py -q
```

Expected: collection or import failure because `poddown.audio.activities` and its public factory are not implemented yet. Do not weaken the scenario or skip it.

- [ ] **Step 4: Extend workflow payloads with the immutable episode snapshot**

In `EpisodeRenderWorkflow._run_segment`, add the `episode` field alongside the existing segment, attempt, take, and activity key:

```python
{
    "episode": episode_input.to_dict(),
    "segment": segment.to_dict(),
    "attempt": attempt,
    "take": take,
    "activity_key": activity_key_for(...),
}
```

Keep the workflow ID, activity ID, retry policy, and activity name unchanged. The new field is required so the activity can recompute and authenticate `activity_key_for` rather than trusting a caller-supplied key.

- [ ] **Step 5: Run the workflow contract tests**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/unit/audio/test_workflow.py tests/bdd/test_temporal_orchestration.py -q
```

Expected: existing workflow and orchestration behavior remains green; the new BDD file remains red only on the missing activity implementation.

- [ ] **Step 6: Commit the acceptance contract and payload boundary**

```bash
git add tests/features/provider_temporal_render.feature tests/bdd/test_provider_temporal_render.py src/poddown/audio/workflow.py tests/unit/audio/test_workflow.py
git commit -m "test(audio): define durable temporal activity behavior"
```

### Task 2: Implement the provider-neutral durable activity factory

**Files:**
- Create: `src/poddown/audio/activities.py`
- Modify: `src/poddown/audio/__init__.py`
- Create: `tests/unit/audio/test_activities.py`

**Interfaces:**
- Consumes: the exact contracts from the interface block above, plus `DurableRenderService.render_takes(..., take_count=1)`, `FilesystemArtifactStore.read`, `diagnose_wav`, and `evaluate_critical_tokens`.
- Produces: a Temporal-decorated activity callable named `poddown.audio.render_segment` that returns `CandidateQuality.to_dict()`.

- [ ] **Step 1: Write focused unit tests for validation and quality**

Cover these concrete cases before implementation:

```python
def test_activity_reconstructs_request_attempt_and_take():
    result = asyncio.run(activity(payload_for(attempt=2, take=1)))
    assert result["candidate_id"] == request_for(attempt=2, take=1).candidate_id
    assert renderer.calls == [request_for(attempt=2, take=1).idempotency_key]

def test_activity_rejects_activity_key_for_different_snapshot():
    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(payload_with_wrong_activity_key()))
    assert error.value.non_retryable is True

def test_activity_maps_rights_failure_before_renderer_dispatch():
    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(payload_with_wrong_consent()))
    assert error.value.type == "RightsFailureError"
    assert renderer.calls == []

def test_deterministic_quality_evaluator_verifies_expected_critical_tokens():
    quality = deterministic_quality_evaluator(
        request, audio_bytes, diagnostics, ("Temporal", "not", "optional")
    )
    assert quality.fidelity.passed is True
    assert quality.pronunciation_passed is True
    assert quality.diagnostics.passes_hard_gates is True
```

- [ ] **Step 2: Run the focused unit tests to confirm RED**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/unit/audio/test_activities.py -q
```

Expected: failure because the activity module and factory are not present.

- [ ] **Step 3: Implement strict payload reconstruction**

The handler must require mappings for `episode` and `segment`, strict integer types for positive `attempt` and non-negative `take`, a non-empty string `activity_key`, and equality between the episode’s segment snapshot and the payload segment. Recompute:

```python
expected_key = activity_key_for(
    episode_input,
    "render",
    segment.segment_id,
    attempt=attempt,
    take=take,
)
if payload["activity_key"] != expected_key:
    raise WorkflowContractError("activity key does not match episode snapshot")
request = replace(segment.render_request, attempt=attempt, take_index=take)
```

- [ ] **Step 4: Implement durable dispatch, artifact QA, and deterministic quality**

Call the injected service exactly as follows:

```python
outcomes = await service.render_takes(
    request,
    segment.consent,
    renderer,
    take_count=1,
)
outcome = outcomes[0]
audio_bytes = artifacts.read(outcome.candidate.artifact)
diagnostics = diagnose_wav(
    audio_bytes,
    expected_sample_rate_hz=request.sample_rate_hz,
    expected_channels=1,
)
quality = evaluator(request, audio_bytes, diagnostics, segment.critical_tokens)
if quality.candidate_id != outcome.candidate.candidate_id:
    raise WorkflowContractError("quality candidate does not match render candidate")
return quality.to_dict()
```

The deterministic-local evaluator calls `evaluate_critical_tokens(critical_tokens, request.expected_spoken_text)`, sets pronunciation to `True` only for this explicit local fixture, and assigns a finite deterministic `Decimal("0")` soft score. It must not claim that a transcription provider was called.

- [ ] **Step 5: Map fail-closed errors without hiding transient failures**

Map `RightsDeniedError` to `ApplicationError(type="RightsFailureError", non_retryable=True)`. Map `RenderRejectedError`, `ArtifactIntegrityError`, `AudioDiagnosticsError`, `CandidateSelectionError`, and `WorkflowContractError` to `ApplicationError(type="MalformedAudioError" or "WorkflowContractError", non_retryable=True)`. Let unexpected `RuntimeError`/`TransientActivityError` escape so Temporal applies the existing retry policy. Do not catch `BaseException`, cancellation, or arbitrary exceptions and relabel them as success.

- [ ] **Step 6: Export the public factory and run unit tests green**

Export `ActivityHandler`, `QualityEvaluator`, `build_durable_render_activity`, and `deterministic_quality_evaluator` from `poddown.audio`. Run:

```bash
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/unit/audio/test_activities.py tests/unit/audio/test_render.py tests/unit/audio/test_storage.py -q
```

Expected: all focused tests pass with no live-provider marker enabled.

- [ ] **Step 7: Commit the activity implementation**

```bash
git add src/poddown/audio/activities.py src/poddown/audio/__init__.py tests/unit/audio/test_activities.py
git commit -m "feat(audio): wire durable render activity"
```

### Task 3: Prove Temporal retry replay and update traceability evidence

**Files:**
- Modify: `tests/bdd/test_provider_temporal_render.py`
- Create: `tests/integration/test_temporal_durable_render_activity.py`
- Create: `docs/verification/provider-temporal-render-activity.md`
- Modify: `docs/planning-traceability.md`

**Interfaces:**
- Consumes: `build_durable_render_activity`, `TemporalEpisodeWorkflowService`, `Worker`, `WorkflowEnvironment`, `FilesystemArtifactStore`, `FilesystemRenderRecordStore`, and `DeterministicLocalRenderer`.
- Produces: executable proof that a post-persist retry reuses the same durable outcome, records one cost event, and does not duplicate renderer usage.

- [ ] **Step 1: Make the BDD happy-path and rights scenarios execute the real handler**

Register the factory result in the local Temporal worker:

```python
handler = build_durable_render_activity(service, renderer, artifacts)
async with (
    await WorkflowEnvironment.start_local() as environment,
    Worker(
        environment.client,
        task_queue=TASK_QUEUE,
        workflows=[EpisodeRenderWorkflow],
        activities=[handler],
    ),
):
    result = await TemporalEpisodeWorkflowService(
        environment.client, TASK_QUEUE
    ).run_episode(episode_input)
```

Use a matching `VoiceConsent` for the success scenario and assert that the filesystem record lookup contains one outcome with one non-null `cost_event`. Use a mismatched provider allow-list for the rights scenario and assert `RightsFailureError` plus zero renderer calls.

- [ ] **Step 2: Add the post-persist transient failure wrapper**

Wrap the real handler in a separately named test activity that raises once after the handler has returned:

```python
failed_keys: set[str] = set()

@activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
async def fail_once_after_persist(payload: dict[str, Any]) -> dict[str, Any]:
    result = await handler(payload)
    key = str(payload["activity_key"])
    if key not in failed_keys:
        failed_keys.add(key)
        raise RuntimeError("simulated worker loss after durable save")
    return result
```

Run the full workflow with this wrapper and assert completion, exactly one renderer call for each of the workflow's three unique take keys, exactly one persisted cost event per candidate, three immutable records total, and unchanged results from a second `run_episode` call.

- [ ] **Step 3: Run the integration and BDD tests**

Run:

```bash
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/bdd/test_provider_temporal_render.py tests/integration/test_temporal_durable_render_activity.py -q
```

Expected: all scenarios pass, including real Temporal retry and completed-workflow replay.

- [ ] **Step 4: Document evidence and honest boundaries**

Record the exact commands and observed results in `docs/verification/provider-temporal-render-activity.md`. State explicitly that this proves deterministic local artifact/cost replay, not hosted provider exactly-once semantics, transcription, mastering, episode packaging, publication, API, CLI, MCP, or production readiness.

- [ ] **Step 5: Update the delivery ledger**

Update `docs/planning-traceability.md` so M2 records:

```markdown
- provider-bound Temporal activity wiring: locally verified with the real
  durable render service, immutable filesystem artifact/record stores, rights
  failure before dispatch, deterministic WAV diagnostics, and post-persist
  retry replay with one local cost event per candidate;
- provider-hosted idempotency, transcription/fidelity provider calls,
  mastering, package commit, and publication remain pending in their assigned
  milestones.
```

- [ ] **Step 6: Commit the integration evidence**

```bash
git add tests/bdd/test_provider_temporal_render.py tests/integration/test_temporal_durable_render_activity.py docs/verification/provider-temporal-render-activity.md docs/planning-traceability.md
git commit -m "test(audio): verify temporal render replay evidence"
```

### Task 4: Review, verify, and prepare the stacked PR

**Files:**
- Modify only files identified by review findings.
- Preserve the parent stack: base `codex/m2-orchestration-qa`, head `codex/m2-provider-temporal-render`.

**Interfaces:**
- Consumes: Tasks 1–3 and the repository verification contracts.
- Produces: a clean, ready-for-review PR with fresh evidence and no live-provider claims.

- [ ] **Step 1: Run the changed-scope verification gate**

Before any push, run:

```bash
PYDANTIC_DISABLE_PLUGINS=1 make check
make build
make docs
uv lock --check
uv pip check
git diff --check
```

Also run focused branch coverage for `audio.activities`, `audio.render`, `audio.storage`, and `audio.workflow` with `--cov-branch --cov-fail-under=80`, plus tracked JSON/YAML validation, `compileall`, and a credential scan. Do not run `make check-full` or `make quality-gates` locally.

- [ ] **Step 2: Request an independent review**

Use `superpowers:requesting-code-review` with the acceptance scenarios, changed-file list, and the exact verification output. Keep the reviewer on Luna/Terra-class capacity; do not use Sol High for this routine slice.

- [ ] **Step 3: Receive and address review feedback rigorously**

Use `superpowers:receiving-code-review`. For every valid finding, add or update a failing focused test first, implement the smallest correction, rerun the focused suite and `make check`, reply with evidence, and only then resolve the corresponding review thread. Leave technically invalid feedback answered with concrete contract/test evidence.

- [ ] **Step 4: Rebase, push, create a normal ready PR, and monitor CI**

Read `/Users/djh/.codex/AGENTS-DELIVERY.md`, fetch the parent branch, rebase without rewriting protected branches, run the final verification gate again, push, and create a non-draft PR titled:

```text
feat(audio): wire provider-bound temporal rendering
```

The PR body must reference Spec 003, state parent PR #7 / base branch, list acceptance behaviors, include verification evidence, identify deterministic-local mode, and list intentional deferrals. Monitor required CI and open review feedback; do not merge or approve autonomously.

- [ ] **Step 5: Update the ledger and continue to the next dependency-ready milestone**

After CI and review are clean, update the traceability evidence and immediately select the next approved M2 slice: transcription-backed fidelity/pronunciation QA or deterministic mastering, whichever is dependency-ready from the repository contracts.

### Task 5: Resolve review-blocking failure and concurrency semantics

**Files:**
- Create: `tests/integration/test_temporal_failure_semantics.py`
- Create: `tests/integration/test_durable_render_concurrency.py`
- Modify: `src/poddown/audio/workflow.py`
- Modify: `src/poddown/audio/render.py`
- Modify: `src/poddown/audio/storage.py` if the record store needs a canonical lock path
- Modify: `docs/verification/provider-temporal-render-activity.md`
- Modify: `docs/planning-traceability.md`

**Interfaces:**
- Consumes: `ActivityError.cause`, `ApplicationError.non_retryable/type`, the existing `NON_RETRYABLE_ERROR_TYPES`, and shared filesystem render stores.
- Produces: terminal structured workflow evidence for non-retryable activity failures and one provider dispatch for concurrent identical local idempotency keys.

- [x] **Step 1: Observe RED workflow failure semantics**

The new Temporal integration must configure a mismatched `VoiceConsent`, `max_attempts=2`, and the real provider-bound activity, then assert:

```python
result = await service.run_episode(episode_input)

assert result.status == "failed"
assert result.decisions[0].attempt == 1
assert result.decisions[0].failure_code == "RightsFailureError"
assert result.terminal_failure is not None
assert result.terminal_failure.failed_gates == ("rights",)
assert renderer.calls == []
assert list((tmp_path / "records").rglob("*.json")) == []
```

Run the focused test and confirm the current broad `ActivityError` branch incorrectly advances to another attempt or reports `ACTIVITY_RETRY_EXHAUSTED`.

- [x] **Step 2: Observe RED concurrent idempotency behavior**

Use two `DurableRenderService` instances sharing one artifact/record store and a renderer whose `render` appends its key, awaits `asyncio.sleep(0)`, then delegates to `DeterministicLocalRenderer`. Assert:

```python
first, second = await asyncio.gather(
    service_a.render_takes(request, consent, renderer),
    service_b.render_takes(request, consent, renderer),
)

assert len(renderer.calls) == 1
assert sorted(outcome.replayed for outcome in (first + second)) == [False, True]
assert records.find(request.idempotency_key).cost_event is not None
```

Run the focused test and confirm the current find-then-render sequence can dispatch the same key twice.

- [x] **Step 3: Terminate non-retryable activity failures at the workflow boundary**

Add a helper that walks `ActivityError.cause` and returns the configured application-error type when the cause is an `ApplicationError` with `non_retryable=True` or a configured `NON_RETRYABLE_ERROR_TYPES` value. In `_run_segment`, return a `SegmentDecision` immediately at the current attempt for that error instead of continuing the repair loop. Preserve transient `ActivityError` behavior and `ACTIVITY_RETRY_EXHAUSTED` for retryable failures. Derive terminal failed gates from the error type so rights failures produce `("rights",)` while malformed audio/contract failures retain their specific gate evidence.

- [x] **Step 4: Add a filesystem-backed per-key claim around provider dispatch**

Expose a canonical lock path from `FilesystemRenderRecordStore`, validate the same `render-[0-9a-f]{64}` key used for records, and create a lock directory below the record root. In `DurableRenderService.render_takes`, acquire all requested key locks in sorted canonical-key order, then re-read each record and render/save or replay while holding the complete claim set. Use a POSIX advisory lock through `fcntl.flock` in `asyncio.to_thread` so separate worker processes cannot race the provider call and process termination releases the locks. Acquiring the complete sorted set prevents cross-request deadlocks while preserving the all-before-dispatch replay-authentication invariant. The provider crash window between dispatch and save remains an explicit provider-idempotency responsibility and must not be documented as solved by the filesystem lock.

- [x] **Step 5: Run the review-hardening tests green**

Run:

```bash
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin tests/integration/test_temporal_failure_semantics.py tests/integration/test_durable_render_concurrency.py tests/integration/test_temporal_durable_render_activity.py tests/unit/audio/test_render.py tests/unit/audio/test_artifacts.py -q
```

Expected: terminal rights failure occurs on attempt one, concurrent identical requests produce one renderer call and one cost record, and the existing three-take post-persist Temporal replay remains green.

- [ ] **Step 6: Update evidence and commit the review correction**

Document the new workflow-terminal and concurrent local-dispatch evidence, state the provider crash-window limitation, run the complete changed-scope verification gate, and commit:

```bash
git add src/poddown/audio/workflow.py src/poddown/audio/render.py src/poddown/audio/storage.py tests/integration/test_temporal_failure_semantics.py tests/integration/test_durable_render_concurrency.py docs/verification/provider-temporal-render-activity.md docs/planning-traceability.md docs/superpowers/plans/2026-08-10-provider-temporal-render-activity.md
git commit -m "fix(audio): harden temporal failure and render claims"
```

### Task 6: Close independent review findings without weakening selection

**Files:**
- Create: `tests/integration/test_durable_render_multiprocess.py`
- Modify: `src/poddown/audio/workflow.py`
- Modify: `tests/bdd/test_provider_temporal_render.py`
- Modify: `tests/features/provider_temporal_render.feature`
- Modify: `tests/integration/test_temporal_failure_semantics.py`
- Modify: verification and traceability evidence

**Interfaces:**
- Consumes: the Spec 003 three-take selection contract, Temporal retry results,
  configured activity-error types, and POSIX filesystem claims.
- Produces: selection evidence that preserves successful takes beside a failed
  sibling, explicit configuration/activity terminal gates, and a bounded
  separate-process claim test.

- [x] **Step 1: Observe the review RED case**

Add a real Temporal BDD scenario where the third take exhausts its transient
activity retry policy while the first two takes return valid candidates. The
pre-fix workflow must fail or enter repair instead of selecting a passing take.

- [x] **Step 2: Preserve successful takes when retryable siblings fail**

Parse successful activity results before handling retryable `ActivityError`
values. Continue to terminate non-retryable errors immediately, but select from
the successful candidate subset before entering segment repair.

- [x] **Step 3: Make unknown terminal failures fail closed with truthful gates**

Map `ActivityNotConfigured` to `("configuration",)` and unknown terminal
activity types to `("activity",)` rather than claiming fidelity, pronunciation,
and audio QA failures. Verify both the default handler and an unknown terminal
type through Temporal BDD and integration tests.

- [x] **Step 4: Prove the filesystem claim across separate POSIX processes**

Use two bounded worker processes and a marker renderer that yields after
dispatch. Require one marker, one durable cost event, and one replayed outcome;
retain the explicit provider crash-window deferral.

- [x] **Step 5: Run focused review-finding verification**

Run the provider BDD, terminal-failure integration, and cross-process claim
tests, then rerun the branch-aware audio module coverage and repository gate.

- [ ] **Step 6: Re-review, commit, and continue the stacked delivery**

Request a fresh independent review on Luna/Terra capacity, resolve any valid
finding with the same RED-test-first discipline, commit the correction, and
continue to the next dependency-ready M2 milestone.

## Self-Review and Explicit Deferrals

- Spec 003 workflow requirements addressed in this plan: immutable activity payload authentication, provider-neutral activity dispatch, rights/capability preflight, up to three-take compatibility through the existing workflow, hard-gated quality return, deterministic diagnostics, durable artifacts, replay-safe local provider usage, and structured non-retryable failures.
- Spec 003 requirements intentionally not claimed by this plan: transcription-provider integration, pronunciation lexicon execution, final-master loudness/peak/duration gates, mastering, package/provenance commit, and publication.
- Spec 004 requirements intentionally not claimed by this plan: asynchronous HTTP API, persistent episode/job model, tenant isolation, object-storage adapter, and provider-cost reconciliation beyond the already durable local cost event.
- Spec 005/006/007/008 and the demo UI remain outside this slice and must retain their pending ledger entries.
- No placeholders, fake hosted-provider success, skipped required tests, credentials, or speculative billing/team/UI infrastructure are introduced.
