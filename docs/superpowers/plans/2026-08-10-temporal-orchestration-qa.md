# Temporal orchestration and audio QA implementation plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` and `superpowers:test-driven-development` task-by-task. Every behavior starts with a failing pytest-bdd scenario or focused unit test.

**Goal:** Extend the durable local render foundation with a Temporal-backed, resumable workflow boundary that retries transient activity failures, renders only failed segments, rejects candidates that miss hard gates, ranks passing candidates deterministically, and records structured terminal evidence.

**Scope:** This is the next dependency-ready slice of `specs/003-durable-audio-production/spec.md`. It covers workflow identity, bounded retry policy, three-take fan-out, hard-gate selection, deterministic soft scoring, partial segment repair, and completed-workflow replay at the orchestration boundary. It does not claim final transcription, provider-backed pronunciation verification, ffmpeg mastering, final episode packaging, publication, or live-provider quality evidence.

**SDK decision:** Use the maintained `temporalio` Python SDK, pinned through the repository lockfile. Default tests use a local Temporal development environment and deterministic local activities; they do not contact a hosted Temporal service or a live audio provider. The time-skipping test server is not used because the SDK documents that it is unsupported on ARM hosts.

## Global constraints

- Workflow inputs, activity inputs, decisions, failures, and results are immutable typed values with stable JSON-compatible identities.
- Workflow code is deterministic: no wall-clock reads, randomness, environment reads, network calls, filesystem writes, provider SDK objects, or mutable global state.
- Activity idempotency keys derive from episode version, stage, segment, attempt, take, and provider inputs. Temporal retrying an activity must reuse the same key and must not create a second accepted candidate or cost event.
- Rights and capability checks remain before every renderer dispatch. Invalid or truncated audio fails before transcription or downstream quality work.
- Candidate selection evaluates every hard gate first. A hard-gate failure can never be rescued by a higher soft score; soft scoring runs only over candidates that pass all hard gates.
- Each attempt produces at most three takes. Accepted segments are never dispatched again during repair; only failed segments receive a new attempt.
- Terminal exhaustion returns structured failure evidence including segment, attempt count, failed gates, and last error code. Silent fallback is forbidden.
- Local deterministic fixtures are clearly labeled as demo mode. No live provider credentials, payment approval, or external Temporal account is required.
- Mastering, final-master transcription, episode-package commit, and blinded listening calibration remain explicit follow-up milestones.

## Contracts

```python
@dataclass(frozen=True)
class SegmentWorkflowInput:
    segment_id: str
    render_request: RenderRequest
    consent: VoiceConsent
    critical_tokens: tuple[str, ...]


@dataclass(frozen=True)
class EpisodeWorkflowInput:
    episode_id: str
    episode_version: str
    segments: tuple[SegmentWorkflowInput, ...]
    max_attempts: int = 2


@dataclass(frozen=True)
class CandidateQuality:
    candidate_id: str
    fidelity: FidelityResult
    diagnostics: AudioDiagnostics
    pronunciation_passed: bool
    soft_score: Decimal


@dataclass(frozen=True)
class SegmentDecision:
    segment_id: str
    attempt: int
    accepted_candidate_id: str | None
    candidates: tuple[CandidateQuality, ...]
    failure_code: str | None


@dataclass(frozen=True)
class EpisodeWorkflowResult:
    workflow_id: str
    status: Literal["completed", "failed"]
    decisions: tuple[SegmentDecision, ...]
    terminal_failure: WorkflowFailure | None
```

The Temporal workflow is `EpisodeRenderWorkflow`. Its activities receive only
these serializable contracts and use the existing `DurableRenderService` and
artifact/cost boundaries. The workflow ID is derived from the immutable episode
and version snapshot; an existing completed workflow is returned rather than
rerun.

## File ownership

- Modify: `pyproject.toml`, `uv.lock`, and `src/poddown/audio/__init__.py`.
- Create: `src/poddown/audio/diagnostics.py` for decoded WAV validation and objective hard/soft metrics in this slice.
- Create: `src/poddown/audio/selection.py` for hard-gate evaluation and deterministic soft ranking.
- Create: `src/poddown/audio/workflow.py` for Temporal workflow/activity definitions and retry policy.
- Create: `src/poddown/audio/orchestration.py` for the application service that starts or reuses a stable workflow ID.
- Create: `tests/features/temporal_orchestration.feature` and `tests/bdd/test_temporal_orchestration.py`.
- Create: `tests/unit/audio/test_diagnostics.py`, `tests/unit/audio/test_selection.py`, and `tests/unit/audio/test_workflow_contracts.py`.
- Create: `tests/integration/test_temporal_orchestration.py` with the local Temporal environment and deterministic activities.
- Create: `docs/verification/temporal-orchestration-qa.md` and update `docs/planning-traceability.md` only for evidence actually present.

## Execution checklist

- [ ] Task 1: write and observe the BDD acceptance scenarios RED.
- [ ] Task 2: write diagnostics, selection, and workflow contract tests RED.
- [ ] Task 3: add the Temporal dependency and immutable orchestration contracts.
- [ ] Task 4: implement diagnostics and hard-gate candidate selection.
- [ ] Task 5: implement Temporal retry, fan-out, repair, and terminal failure semantics.
- [ ] Task 6: prove local Temporal integration, restart recovery, and completed-workflow replay.
- [ ] Task 7: verify, independently review, document, commit, push, and open a ready PR.

## Tasks

### Task 1: Write executable acceptance coverage first

- Add scenarios for bounded three-take fan-out, hard-gate precedence, transient retry with one accepted cost event, partial repair without rerendering accepted segments, and completed workflow replay.
- Bind the scenarios to a local deterministic fixture and observe the expected RED state before production implementation.
- Do not add production stubs merely to make collection pass.

### Task 2: Define diagnostics, candidate-selection, and workflow contract tests

- Test valid WAV decoding, malformed/truncated audio rejection, sample-rate/channel/duration bounds, clipping and silence diagnostics, and stable metric serialization.
- Test hard-gate precedence, deterministic tie-breaking, stable weighted soft score, rejection of pronunciation/fidelity failures, and structured exhaustion evidence.
- Test stable workflow IDs and activity keys across equal inputs; changing version, segment, attempt, or take must change the relevant key.

### Task 3: Add the Temporal dependency and immutable orchestration contracts

- Use the dependency advisor's recommended `temporalio` version within a `<2` constraint and regenerate the lockfile.
- Implement serializable frozen workflow DTOs and explicit error types.
- Keep Temporal imports isolated to the workflow/orchestration boundary; existing provider and content modules remain independent.

### Task 4: Implement diagnostics and hard-gate selection

- Decode local WAV bytes with standard-library primitives and reject malformed, empty, truncated, unsupported, or metadata-mismatched audio before downstream work.
- Reuse existing critical-token fidelity contracts and represent pronunciation as an explicit gate rather than an implicit score.
- Compute a documented deterministic soft score only after all hard gates pass; sort ties by stable candidate identity.

### Task 5: Implement Temporal workflow and activity retry/repair semantics

- Define `EpisodeRenderWorkflow` with stable workflow ID derivation, bounded retry policy for transient activity failures, non-retryable handling for malformed audio and rights failures, and deterministic segment/take ordering.
- Use activity keys derived from immutable request identity so Temporal retries reuse the persisted render outcome and cost event.
- Fan out up to three takes per attempt, select only hard-gate-passing candidates, and rerender failed segments only on the next attempt.
- Return structured terminal failure after attempt exhaustion; do not synthesize a success result.

### Task 6: Add local Temporal integration and restart/replay evidence

- Run the workflow against the SDK's local development test environment with deterministic activities and no hosted service.
- Prove transient retry, worker/activity failure recovery, accepted-segment preservation during partial repair, stable cost counts, and completed-workflow replay.
- Keep local-server lifecycle bounded and ensure tests clean up workers and temporary artifacts.

### Task 7: Verify, review, and hand off

- Run focused BDD/unit/integration tests, changed-scope `make check`, build, docs, lock, schema, compile, diff, and credential checks.
- Record actual evidence and explicit deferrals in the verification document and ledger.
- Obtain an independent low-cost code review; fix every valid Important/Critical or acceptance-blocking finding with a regression test first.
- Read `/Users/djh/.codex/AGENTS-DELIVERY.md`, commit, push, and open a normal ready PR stacked on `codex/m2-durable-audio-foundation`.

## Explicit deferrals

- Live ElevenLabs/OpenAI rendering, transcription API calls, provider billing reconciliation, ffmpeg mastering, final-master QA, package manifests, chapters, show notes, publication, and blinded listening are later M2 tasks.
- The local Temporal test environment is deterministic integration evidence, not proof of hosted Temporal deployment capacity.
- Database persistence, tenant isolation, FastAPI job/status endpoints, object storage, outbox, and Compose belong to M3.
