# Durable Audio Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a deterministic, replay-safe single-segment audio render foundation that persists immutable content-addressed artifacts and cost evidence while refusing dispatch without valid voice rights or provider capabilities.

**Architecture:** Add a focused `poddown.audio` package instead of changing the existing M1 content and compatibility facades. A frozen render request derives its idempotency and candidate identities from episode/version/stage/segment/attempt/take and provider inputs; a fail-closed rights/capability policy runs before a renderer call. A deterministic local renderer produces fixture audio, a filesystem artifact store persists bytes by SHA-256 without overwrites, and a filesystem render ledger makes completed candidates and cost events replayable across service instances.

**Tech Stack:** Python 3.11+, frozen dataclasses, `pathlib`, `hashlib`, `json`, `wave`, pytest, pytest-bdd, pytest-cov, Ruff, strict mypy, existing `ProviderCapabilities` and `ProviderUsage` contracts.

## Global Constraints

- Implement only the first dependency-ready slice of `specs/003-durable-audio-production/spec.md`; do not claim Temporal, transcription, diagnostics, mastering, packaging, publishing, or live-provider behavior in this plan.
- The provider boundary remains injectable and provider-neutral; the default renderer is deterministic local fixture mode with zero cost and explicit usage metadata.
- Voice consent is required, must match the requested voice asset, must contain evidence, and must explicitly allow the requested provider; missing or revoked rights fail closed before dispatch.
- Provider capabilities must support WAV, the requested sample rate, the complete expected-spoken text, model pinning, voice pinning, and provider idempotency before dispatch.
- Render requests, candidates, artifact references, and cost events are immutable frozen values; malformed audio bytes and mismatched metadata are rejected before acceptance.
- Content-addressed artifacts are immutable; an existing digest is reused only after its bytes are verified, and a conflicting/corrupt object is an error rather than an overwrite.
- Replaying the same request key returns the persisted candidate without a renderer call or second cost event; a different attempt or take index creates a distinct candidate identity.
- New behavior requires executable Gherkin scenarios before production implementation and focused unit/integration coverage afterward.
- Use no live provider credentials, network calls, Temporal server, ffmpeg, or payment approval in this slice.

---

## File Map

- Create `tests/features/durable_audio.feature` with the acceptance scenarios for rights, capability checks, multi-take rendering, immutable artifacts, and replay.
- Create `tests/bdd/test_durable_audio.py` with pytest-bdd fixtures and step definitions that exercise the public audio service.
- Create `tests/unit/audio/test_contracts.py` for identity, validation, and immutable value behavior.
- Create `tests/unit/audio/test_rights.py` for fail-closed consent decisions.
- Create `tests/unit/audio/test_artifacts.py` for digest paths, deduplication, corruption, and missing-object failures.
- Create `tests/unit/audio/test_render.py` for provider preflight, metadata validation, takes, and replay-safe metering.
- Create `tests/integration/test_durable_render.py` for persistence across service instances and an end-to-end three-take local render.
- Create `src/poddown/audio/__init__.py` with the small public surface used by tests and later workflow adapters.
- Create `src/poddown/audio/contracts.py` with frozen render, artifact, candidate, usage, and cost-event values plus the renderer protocol.
- Create `src/poddown/audio/rights.py` with consent values and the fail-closed rights policy.
- Create `src/poddown/audio/storage.py` with artifact and render-record protocols and filesystem implementations.
- Create `src/poddown/audio/local.py` with the deterministic local renderer fixture.
- Create `src/poddown/audio/render.py` with capability preflight, candidate validation, durable render orchestration, and replay behavior.
- Modify `docs/planning-traceability.md` only after tests and verification pass, adding evidence for this M2 slice and leaving later M2 requirements pending.
- Create `docs/verification/durable-audio-foundation.md` with exact commands, fixture mode, artifact/replay evidence, and explicit deferred capabilities.

## Interfaces

The implementation must expose these names and signatures:

```python
# poddown.audio.contracts
@dataclass(frozen=True)
class ArtifactRef:
    sha256: str
    media_type: str
    size_bytes: int
    relative_path: str

@dataclass(frozen=True)
class RenderRequest:
    episode_id: str
    episode_version: str
    segment_id: str
    speaker_id: str
    expected_spoken_text: str
    voice_asset_id: str
    provider: str
    model: str
    attempt: int = 1
    take_index: int = 0
    output_format: str = "wav"
    sample_rate_hz: int = 44_100

    @property
    def idempotency_key(self) -> str: ...

    @property
    def candidate_id(self) -> str: ...

@dataclass(frozen=True)
class RenderedAudio:
    audio_bytes: bytes
    provider: str
    model: str
    request_id: str
    usage: ProviderUsage
    cost: Decimal
    output_format: str
    sample_rate_hz: int

@dataclass(frozen=True)
class ProviderCostEvent:
    event_id: str
    candidate_id: str
    provider: str
    usage: ProviderUsage
    cost: Decimal

@dataclass(frozen=True)
class RenderCandidate:
    candidate_id: str
    idempotency_key: str
    segment_id: str
    speaker_id: str
    attempt: int
    take_index: int
    voice_asset_id: str
    expected_spoken_text: str
    provider: str
    model: str
    request_id: str
    usage: ProviderUsage
    cost: Decimal
    artifact: ArtifactRef

@dataclass(frozen=True)
class RenderOutcome:
    candidate: RenderCandidate
    cost_event: ProviderCostEvent | None
    replayed: bool

class AudioRenderer(Protocol):
    capabilities: ProviderCapabilities
    async def render(self, request: RenderRequest) -> RenderedAudio: ...

# poddown.audio.rights
@dataclass(frozen=True)
class VoiceConsent:
    voice_asset_id: str
    evidence_id: str
    allowed_providers: frozenset[str]
    valid: bool = True

def require_render_rights(request: RenderRequest, consent: VoiceConsent | None) -> None: ...

# poddown.audio.storage
class ArtifactStore(Protocol):
    def put(self, audio_bytes: bytes, *, media_type: str) -> ArtifactRef: ...
    def read(self, artifact: ArtifactRef) -> bytes: ...

class RenderRecordStore(Protocol):
    def find(self, idempotency_key: str) -> RenderOutcome | None: ...
    def save(self, outcome: RenderOutcome) -> None: ...

class FilesystemArtifactStore:
    def __init__(self, root: Path) -> None: ...

class FilesystemRenderRecordStore:
    def __init__(self, root: Path, artifacts: ArtifactStore | None = None) -> None: ...

# poddown.audio.render
class DurableRenderService:
    def __init__(self, artifacts: ArtifactStore, records: RenderRecordStore) -> None: ...
    async def render_takes(
        self,
        request: RenderRequest,
        consent: VoiceConsent | None,
        renderer: AudioRenderer,
        *,
        take_count: int = 1,
    ) -> tuple[RenderOutcome, ...]: ...
```

### Task 1: Write the durable audio acceptance tests first

**Files:**
- Create: `tests/features/durable_audio.feature`
- Create: `tests/bdd/test_durable_audio.py`

**Interfaces:**
- Consumes: the public names in the interface block above; step definitions may import them before they exist.
- Produces: executable failing acceptance coverage for later tasks.

- [x] **Step 1: Write the Gherkin scenarios before production code.** Include exactly these behaviors:

```gherkin
Feature: Durable single-segment rendering

  Scenario: Render three deterministic takes for a rights-cleared segment
    Given a rights-cleared render request
    When the request is rendered with three local takes
    Then three immutable candidates and artifacts are returned
    And every candidate preserves the expected-spoken text
    And every candidate records zero-cost local usage

  Scenario: Refuse a request without valid voice consent before dispatch
    Given a render request without voice consent
    When the request is rendered with one local take
    Then rendering is rejected before the renderer is called
    And no artifact or cost event is recorded

  Scenario: Refuse a provider capability mismatch before dispatch
    Given a rights-cleared render request
    And the renderer cannot pin the requested voice
    When the request is rendered with one local take
    Then rendering is rejected before the renderer is called
    And no artifact or cost event is recorded

  Scenario: Replay returns the immutable candidate without duplicate cost
    Given a rights-cleared render request
    When the same request is rendered twice with one local take
    Then the second result is marked as replayed
    And the renderer is called only once
    And exactly one cost event exists

  Scenario: A new take has a distinct immutable identity
    Given a rights-cleared render request
    When the request is rendered with two local takes
    Then the two candidates have different candidate identities
    And the two artifacts have different content digests
```

- [x] **Step 2: Add step definitions with a per-scenario temporary store.** The fixture must construct `RenderRequest(episode_id="demo-episode", episode_version="v1", segment_id="segment-001", speaker_id="host", expected_spoken_text="The rate is 13.9 hertz, not 14 hertz.", voice_asset_id="voice-host-v1", provider="local", model="local-deterministic-v1")`, `VoiceConsent("voice-host-v1", "consent-demo-001", frozenset({"local"}))`, `FilesystemArtifactStore(tmp_path / "artifacts")`, `FilesystemRenderRecordStore(tmp_path / "records")`, `DurableRenderService(...)`, and `DeterministicLocalRenderer()`.
- [x] **Step 3: Run the BDD file and confirm it fails for the expected missing-module reason.**

Run: `PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src uv run pytest tests/bdd/test_durable_audio.py -q`

Expected: collection fails because `poddown.audio` does not exist yet; do not add production stubs solely to make collection pass.

- [x] **Step 4: Commit the red acceptance tests.**

```bash
git add tests/features/durable_audio.feature tests/bdd/test_durable_audio.py
git commit -m "test(audio): define durable render acceptance"
```

### Task 2: Add immutable contracts and fail-closed rights policy

**Files:**
- Create: `src/poddown/audio/__init__.py`
- Create: `src/poddown/audio/contracts.py`
- Create: `src/poddown/audio/rights.py`
- Create: `tests/unit/audio/test_contracts.py`
- Create: `tests/unit/audio/test_rights.py`

**Interfaces:**
- Consumes: existing `poddown.domain.ProviderUsage` and `poddown.providers.contracts.ProviderCapabilities`.
- Produces: all values and `require_render_rights` in the interface block; later storage and rendering tasks import them.

- [x] **Step 1: Write unit tests for canonical identity and validation.** Assert that two equal frozen requests have equal keys and candidate IDs; changing `attempt` or `take_index` changes both; empty IDs/text, non-positive attempt, negative take, unsupported output format, invalid sample rate, and non-positive/boolean usage are rejected with `ValueError`; `RenderedAudio` rejects empty bytes, provider/model mismatches only in service validation, invalid cost, and invalid usage.
- [x] **Step 2: Write rights tests before implementation.** Assert `require_render_rights` raises `RightsDeniedError` for `None`, invalid consent, missing evidence, mismatched asset, and provider absent from `allowed_providers`; assert a complete matching consent returns `None`.
- [x] **Step 3: Implement frozen dataclasses and canonical JSON hashing.** Serialize only stable request fields with sorted keys and compact separators; hash with SHA-256; use `candidate-{full_digest}` and never UUID/random values for identity.
- [x] **Step 4: Implement the rights policy with a specific error per fail-closed reason.** Check consent presence, `valid is True`, non-empty evidence, matching asset, and explicit provider allow-list in that order; do not call or inspect a renderer in this module.
- [x] **Step 5: Run focused unit tests and then the existing rendering tests.**

Run: `PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src uv run pytest tests/unit/audio/test_contracts.py tests/unit/audio/test_rights.py tests/unit/test_rendering.py -q`

Expected: new contract/rights tests pass; the existing M1 compatibility rendering tests remain green.

- [x] **Step 6: Commit the contract and policy slice.**

```bash
git add src/poddown/audio tests/unit/audio/test_contracts.py tests/unit/audio/test_rights.py
git commit -m "feat(audio): add immutable render and rights contracts"
```

### Task 3: Persist immutable content-addressed artifacts and render records

**Files:**
- Create: `src/poddown/audio/storage.py`
- Create: `tests/unit/audio/test_artifacts.py`

**Interfaces:**
- Consumes: `ArtifactRef`, `RenderOutcome`, `RenderCandidate`, and `ProviderCostEvent` from `poddown.audio.contracts`.
- Produces: `FilesystemArtifactStore` and `FilesystemRenderRecordStore` implementing the protocols used by `DurableRenderService`.

- [x] **Step 1: Write artifact-store tests.** Assert `put(b"audio", media_type="audio/wav")` returns the SHA-256 digest, byte size, and a path under the configured root; putting the same bytes returns the same reference without creating another object; `read` returns bytes; missing, path-escaping, digest-mismatch, and size-mismatch references raise `ArtifactIntegrityError`; writing a corrupted object never silently repairs or overwrites it.
- [x] **Step 2: Write render-record tests.** Save an outcome and load it from a new `FilesystemRenderRecordStore` instance; assert all nested metadata and the cost event round-trip; saving the exact same outcome is idempotent; saving a different outcome under one idempotency key raises `IdempotencyConflictError`; a missing or malformed JSON record raises `ArtifactIntegrityError`.
- [x] **Step 3: Implement atomic no-overwrite artifact writes.** Use a SHA-256-derived relative path under `root/artifacts/`, create parent directories, write to a same-directory temporary file, link into the final path without replacement, and compare existing bytes before returning an existing reference.
- [x] **Step 4: Implement strict reference validation and JSON record serialization.** Keep records under `root/records/{idempotency_key}.json`; require record keys to be one canonical SHA-256-like path component, derive artifact references only from `artifacts/<digest-prefix>/<digest>.<extension>`, verify artifact bytes during render replay through the injected `ArtifactStore` (defaulting to the sibling `artifacts` store used by the demo fixture), serialize Decimal values as strings, and reconstruct frozen values with explicit field parsing rather than `eval` or pickle.
- [x] **Step 5: Run focused storage tests.**

Run: `PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src uv run pytest tests/unit/audio/test_artifacts.py -q`

Expected: all filesystem immutability, integrity, and persistence tests pass.

- [x] **Step 6: Commit the storage slice.**

```bash
git add src/poddown/audio/storage.py tests/unit/audio/test_artifacts.py
git commit -m "feat(audio): persist immutable render artifacts"
```

### Task 4: Implement deterministic local rendering and durable orchestration

**Files:**
- Create: `src/poddown/audio/local.py`
- Create: `src/poddown/audio/render.py`
- Modify: `src/poddown/audio/__init__.py`
- Create: `tests/unit/audio/test_render.py`

**Interfaces:**
- Consumes: contracts, rights policy, `FilesystemArtifactStore`/`FilesystemRenderRecordStore` protocols, and existing provider capabilities.
- Produces: `DeterministicLocalRenderer` and `DurableRenderService.render_takes(...)` used by BDD and integration tests.

- [x] **Step 1: Write renderer and service tests before implementation.** Cover three takes, rights rejection before `renderer.calls` changes, capability rejection before dispatch, empty bytes rejection, provider/model/format/sample-rate/usage/cost mismatch rejection, replay from the same filesystem stores without a second renderer call, partial replay where only a missing take dispatches, and distinct attempt/take identities.
- [x] **Step 2: Implement `DeterministicLocalRenderer`.** Expose WAV/44.1 kHz, model and voice pinning, a high text limit, timestamps disabled, and provider idempotency; implement `async def render(request)` and generate a deterministic valid PCM WAV using `wave` from a digest-derived frame count; return `local-{key-prefix}` request IDs, exact request provider/model/format/rate, character/byte usage, and `Decimal("0")` cost; append each request key to `calls`.
- [x] **Step 3: Implement service preflight and take expansion.** Validate `1 <= take_count <= 3`; require rights; require requested format/sample rate/text length/model pinning/voice pinning/provider idempotency; create `replace(request, take_index=request.take_index + offset)` for each take; look up and verify existing records/artifacts before any renderer call.
- [x] **Step 4: Implement the new-render path.** Await the injected renderer exactly once per missing take, validate non-empty bytes and exact normalized metadata, persist bytes through `ArtifactStore`, construct one candidate and one cost event whose `event_id` equals `cost-{candidate_id}`, save the immutable outcome, and return `replayed=False`.
- [x] **Step 5: Implement replay and integrity behavior.** Return `RenderOutcome(replayed=True, cost_event=None)` for an intact record; verify the stored artifact digest/size before returning; propagate an integrity error for missing/corrupt artifacts without dispatching a replacement or creating a duplicate cost event.
- [x] **Step 6: Run focused render and BDD tests.** The synchronous BDD steps must invoke the async service with `asyncio.run` so the executable feature remains compatible with the repository's current pytest-bdd setup.

Run: `PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src uv run pytest tests/unit/audio/test_render.py tests/bdd/test_durable_audio.py -q`

Expected: all new durable audio scenarios and unit tests pass, with the existing provider-rendering scenarios still passing.

- [x] **Step 7: Commit the orchestration slice.**

```bash
git add src/poddown/audio/__init__.py src/poddown/audio/local.py src/poddown/audio/render.py tests/unit/audio/test_render.py
git commit -m "feat(audio): add replay-safe local segment rendering"
```

### Task 5: Verify persistence end to end and document the bounded milestone

**Files:**
- Create: `tests/integration/test_durable_render.py`
- Create: `docs/verification/durable-audio-foundation.md`
- Modify: `docs/planning-traceability.md`
- Modify: `docs/product-delivery-plan.md` only where the current M2 row needs a link to the evidence document.

**Interfaces:**
- Consumes: the complete public audio foundation and the local fixture; does not add new production behavior.
- Produces: repeatable verification evidence and traceability for only the implemented M2 requirements.

- [x] **Step 1: Write the integration test.** Render a rights-cleared request with three takes into a temporary filesystem, record artifact/cost/call counts, construct a fresh service and renderer over the same store, replay all three takes, and assert candidates, SHA-256s, cost events, and artifact bytes are identical while the fresh renderer has zero calls; then render `attempt=2` and assert a new identity and artifact.
- [x] **Step 2: Run the integration test red/green with the full changed-scope test selection.**

Run: `PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src uv run pytest tests/integration/test_durable_render.py tests/bdd/test_durable_audio.py tests/unit/audio tests/unit/test_rendering.py -q --cov=poddown.audio --cov-branch --cov-report=term-missing`

Expected: all selected tests pass and the new audio modules have branch coverage for preflight rejection, new render, replay, and integrity failure paths.

- [x] **Step 3: Run repository changed-scope checks.**

Run: `PYDANTIC_DISABLE_PLUGINS=1 PYTHONPATH=src make check`

Expected: Ruff, strict mypy, BDD/unit/integration tests, and branch coverage pass; if the host stalls in the known iCloud-backed environment, record the exact command and state rather than claiming success.

- [x] **Step 4: Run build, formatting, diff, and focused mutation checks.**

Run: `PYDANTIC_DISABLE_PLUGINS=1 PYTHONPATH=src make build`, `uv run ruff format --check src tests`, `uv run ruff check src tests`, and `git diff --check`.

Expected: build and static checks pass; no generated artifact or credential enters the diff.

- [x] **Step 5: Write the verification document with actual evidence.** State that this slice proves rights/capability preflight, deterministic local multi-take rendering, content-addressed immutable artifacts, provider usage/cost recording, and replay idempotency; state that live providers, Temporal, transcription, diagnostics, mastering, packaging, CLI/API/MCP, and publishing are deferred to later plans.
- [x] **Step 6: Update traceability only for evidence that is present.** Link the M2 foundation rows to the feature, unit/integration tests, and verification document; leave the remaining M2 and all later roadmap rows pending.
- [x] **Step 7: Commit the evidence slice.**

```bash
git add tests/integration/test_durable_render.py docs/verification/durable-audio-foundation.md docs/planning-traceability.md docs/product-delivery-plan.md
git commit -m "docs(audio): record durable foundation evidence"
```

### Task 6: Review and release the stacked milestone

**Files:**
- Modify only files identified by an actionable review finding; do not mix unrelated cleanup.

- [x] **Step 1: Run an independent Terra/Luna review against the M2 spec and this plan.** Require findings to cite file/line, severity, violated contract, and a concrete test or fix; use a Sol review only if a security, persistence-integrity, or architectural ambiguity cannot be resolved by the existing contracts.
- [x] **Step 2: For every valid Important/Critical or acceptance-blocking finding, add a failing regression test first, implement the smallest fix, and rerun focused tests.** Do not weaken gates or hide review output.
- [x] **Step 3: Run `PYDANTIC_DISABLE_PLUGINS=1 PYTHONPATH=src make check`, `make build`, `git diff --check`, and the focused BDD/integration selection again before publication.**
- [x] **Step 4: Update the hidden delivery ledger and plan checkboxes with commit SHAs, test output, review rounds, and any host-only verification limitation.**
- [ ] **Step 5: Read `/Users/djh/.codex/AGENTS-DELIVERY.md`, commit/push the branch, and create a normal ready PR stacked on `codex/m1-content-intelligence`; include specs, acceptance behaviors, verification evidence, demo instructions, deferred work, and the parent PR link.**

## Plan self-review

- The durable-audio workflow contract is addressed in later plans; this foundation implements the required immutable input identity, pre-dispatch rights/capability gates, artifact persistence, and replay semantics without pretending to complete acceptance behaviors that require transcription or mastering.
- Acceptance behavior 2 and 8 are covered by filesystem record replay; behavior 3, 4, 5, 6, and 7 remain explicitly pending because they require Temporal retry policy, hard-gate scoring, diagnostics, mastering, and final-master verification.
- Every new public type and function used by a later task is defined in the interface block, and task write sets are disjoint except for the explicit `__init__.py` export update.
- No placeholder tasks or speculative live-provider dependencies are present; all commands are concrete and host-stall limitations are recorded as evidence rather than converted into passes.
