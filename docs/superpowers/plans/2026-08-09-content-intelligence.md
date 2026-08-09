# Content Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn an immutable Markdown source and validated profile into a deterministic, source-bound, pronunciation-aware, two-speaker canonical script and capability-safe segmentation manifest for the PodDown M1 vertical slice.

**Architecture:** Add a focused `poddown.content` package containing immutable models, source block indexing, profile resolution, layered lexicon resolution, critical-token extraction, anchored adaptation, deterministic repair, segmentation, and one orchestration service. The reasoning boundary is a structured proposal port; the default local implementation is fixture-backed and deterministic, so M1 can prove source fidelity without live model calls or provider credentials.

**Tech Stack:** Python 3.12+, uv, Pydantic 2, PyYAML, frozen dataclasses, pytest, pytest-bdd, pytest-cov, Ruff, strict mypy.

## Global Constraints

- The Markdown source and complete frontmatter remain immutable and authoritative for factual claims.
- Every factual script turn has at least one valid source anchor; unsupported, contradictory, or unanchored claims fail before any renderer call.
- Dialogue turns use stable IDs, explicit speaker IDs, source anchors, and claim anchors; editorial turns contain no externally verifiable claim.
- Pronunciation precedence is episode override, project lexicon, domain lexicon, then global lexicon; same-priority conflicts fail closed.
- Critical tokens include names, organizations, products, acronyms, technical terms, numbers, currencies, percentages, dates, units, ticker-like symbols, and every negation occurrence; repeated occurrences retain identity.
- Segments contain contiguous complete turns, preserve order and source groupings, never split a critical token, and stay within declared renderer capabilities.
- Identical inputs and capability sets produce equivalent canonical manifests.
- The default implementation must make zero paid provider calls and must not add vendor SDKs or mutate published lexicons.
- Every behavior starts with an executable Gherkin scenario, is observed failing for the intended missing behavior, then follows red-green-refactor.
- Core line and branch coverage remain at least 80%; changed M1 modules target at least 95% coverage where practical.
- Existing M0 public interfaces and provider hard gates remain backward compatible.
- Do not add billing, teams, UI, Kubernetes, or automatic lexicon learning; metering and persistence belong to later milestones.

---

## File map

### New production modules

- `src/poddown/content/__init__.py` — public M1 exports only.
- `src/poddown/content/models.py` — immutable source, profile, treatment, script, lexicon, token, and segment values.
- `src/poddown/content/source.py` — Markdown source hashing, frontmatter preservation, block indexing, and anchor validation.
- `src/poddown/content/profiles.py` — strict YAML profile loading and document-overridable metadata resolution.
- `src/poddown/content/lexicon.py` — immutable layered lexicons and deterministic pronunciation resolution.
- `src/poddown/content/tokens.py` — critical-token extraction and expected-spoken-form construction.
- `src/poddown/content/adaptation.py` — structured reasoning port, anchor validation, dialogue policy, and bounded turn repair.
- `src/poddown/content/segmentation.py` — deterministic capability-aware segmentation.
- `src/poddown/content/service.py` — source-to-segmentation orchestration and canonical manifest hashing.

### M1 tests and fixtures

- `tests/features/content_intelligence.feature` — readable acceptance contract for the full M1 behavior.
- `tests/bdd/test_content_intelligence.py` — executable pytest-bdd bindings; written once in Task 1 and not modified by later tasks.
- `tests/unit/content/test_source.py` — source blocks, hashes, and anchors.
- `tests/unit/content/test_profiles.py` — profile validation and frontmatter override rules.
- `tests/unit/content/test_lexicon.py` — precedence, normalization, conflicts, and version records.
- `tests/unit/content/test_tokens.py` — token categories, spans, expected speech, and occurrence identity.
- `tests/unit/content/test_adaptation.py` — source-bound adaptation, dialogue policy, and repair.
- `tests/unit/content/test_segmentation.py` — contiguous turn grouping and deterministic boundaries.
- `tests/integration/test_content_pipeline.py` — one complete deterministic preparation path and replay parity.
- `tests/evals/test_content_intelligence.py` — adversarial unsupported-claim and token-recall corpus.
- `tests/fixtures/content/robotics-mapping.md` — difficult non-finance reference source with two-speaker, technical, numeric, date, percentage, repeated-negation, and disagreement material.
- `tests/fixtures/content/technical-dialogue-profile.yaml` — versioned two-speaker profile with explicit document-overridable fields.
- `tests/fixtures/content/robotics-adaptation.json` — deterministic structured proposal keyed to the reference source snapshot.
- `tests/fixtures/content/adversarial-adaptation.json` — unsupported, changed-number, and changed-negation proposals.
- `docs/verification/content-intelligence.md` — command/evidence record and requirement traceability for M1.

## Task 1: Author the M1 acceptance contract first

**Files:**
- Create: `tests/features/content_intelligence.feature`
- Create: `tests/bdd/test_content_intelligence.py`
- Create: `tests/fixtures/content/robotics-mapping.md`
- Create: `tests/fixtures/content/technical-dialogue-profile.yaml`
- Create: `tests/fixtures/content/robotics-adaptation.json`

**Interfaces:**
- The bindings call the future public `poddown.content.service.prepare_content(...)`, `poddown.content.lexicon.resolve_pronunciation(...)`, `poddown.content.tokens.extract_critical_tokens(...)`, and `poddown.content.segmentation.segment_script(...)` ports by name only.
- The fixture-backed proposal contains explicit stable turn IDs, two speaker IDs, source block anchors, claim anchors, and a deterministic disagreement turn.

- [x] **Step 1: Write the failing feature scenarios.** Include these scenarios and observable assertions:

```gherkin
Feature: Prepare source-bound technical content

  Scenario: Prepare a difficult two-speaker technical source
    Given the robotics mapping source and technical dialogue profile
    And a deterministic source-bound adaptation proposal
    When content intelligence prepares the episode
    Then the canonical script has two stable speakers and complete factual anchors
    And the script contains disagreement without unsupported claims
    And every extracted critical token has an expected spoken form
    And the segmentation manifest preserves turn order and source grouping

  Scenario: Reject a changed number before rendering
    Given the robotics mapping source and a proposal that changes a source number
    When content intelligence prepares the episode
    Then adaptation fails with an unsupported claim error
    And no renderer call is made

  Scenario: Resolve pronunciation layers deterministically
    Given four lexicon layers for the key "C1"
    When the pronunciation is resolved
    Then the episode layer wins and its version and entry ID are recorded

  Scenario: Reject a same-priority pronunciation conflict
    Given two project lexicon entries for the normalized key "LiDAR"
    When the project pronunciation is resolved
    Then lexicon resolution fails closed with a conflict error

  Scenario: Preserve repeated negation and critical-token occurrences
    Given the text "The system is not silent, and it is not stable"
    When critical tokens are extracted
    Then two distinct negation occurrences are present
    And the token manifest is deterministic

  Scenario: Reject a provider capability that would split a complete turn
    Given a canonical script with a turn longer than the renderer text limit
    When the script is segmented
    Then segmentation fails with a capability error
```

- [x] **Step 2: Bind each scenario to real public ports.** Store source, profile, proposal, layer fixtures, and result in the existing `ScenarioContext`. Do not duplicate validation logic in steps. Keep imports inside `when` steps only where the missing module needs to produce a clean expected red failure.
- [x] **Step 3: Run the focused BDD file.**

Run: `uv run pytest tests/bdd/test_content_intelligence.py -v`

Expected: FAIL because the M1 public modules do not exist yet; the failure must identify a missing `poddown.content` behavior rather than a malformed Gherkin fixture or step setup error.

- [x] **Step 4: Commit the acceptance contract.**

```bash
git add tests/features/content_intelligence.feature tests/bdd/test_content_intelligence.py tests/fixtures/content
git commit -m "test(content): define M1 intelligence acceptance behavior"
```

## Task 2: Add immutable source, profile, and script foundations

**Files:**
- Create: `src/poddown/content/__init__.py`
- Create: `src/poddown/content/models.py`
- Create: `src/poddown/content/source.py`
- Create: `src/poddown/content/profiles.py`
- Create: `tests/unit/content/test_source.py`
- Create: `tests/unit/content/test_profiles.py`

**Interfaces:**
- `SourceBlock(block_id: str, kind: Literal[...], text: str, start: int, end: int)` is frozen and uses half-open byte offsets into the UTF-8 source snapshot.
- `SourceSnapshot(source: str, source_sha256: str, frontmatter: Mapping[str, object], blocks: tuple[SourceBlock, ...])` is frozen and preserves the exact original source string.
- `SourceAnchor(block_id: str, start: int, end: int)` identifies a range within one block; `anchor_text(snapshot, anchor) -> str` rejects unknown blocks and out-of-range spans.
- `Profile(profile_id: str, version: str, format_type: Literal["narration", "dialogue"], target_minutes: int, speakers: tuple[SpeakerProfile, ...], style: Mapping[str, object], audio: Mapping[str, object], quality: Mapping[str, object], document_overridable: frozenset[str])` is immutable.
- `load_profile(yaml_text: str, active_voice_assets: Collection[VoiceAsset], valid_consents: Collection[VoiceConsent]) -> Profile` rejects unknown keys, invalid ranges, missing speakers, inactive assets, revoked consent, and dialogue profiles with fewer than two speakers.
- `resolve_profile_metadata(profile: Profile, frontmatter: Mapping[str, object]) -> Profile` applies only explicitly document-overridable fields and returns a new value.
- `ScriptTurn(turn_id: str, speaker_id: str, text: str, kind: Literal["factual", "editorial"], source_anchors: tuple[SourceAnchor, ...], claim_anchors: tuple[SourceAnchor, ...])` and `ScriptVersion(script_id: str, source_sha256: str, profile_id: str, turns: tuple[ScriptTurn, ...], canonical_hash: str)` are immutable.

- [x] **Step 1: Write unit tests for exact source preservation, deterministic block IDs, valid/invalid anchors, profile constraints, consent rejection, override allowlists, and frozen script values.** Expected values must be hand-derived literals.
- [x] **Step 2: Run the unit tests and verify the expected red failures.**

Run: `uv run pytest tests/unit/content/test_source.py tests/unit/content/test_profiles.py -v`

Expected: FAIL with missing `poddown.content` models/source/profile behavior, not with test collection or fixture errors.

- [x] **Step 3: Implement the minimum immutable models and source index.** Use `hashlib.sha256(source.encode("utf-8"))`, preserve complete YAML frontmatter separately from the PodDown object, and index headings, paragraphs, lists, block quotes, tables, and fenced code without rewriting source text. Use deterministic block IDs derived from block order and content hash.
- [x] **Step 4: Implement strict profile loading.** Use Pydantic validation at the YAML boundary, keep provider IDs out of profile content, and require active voice assets with current consent without making a provider call.
- [x] **Step 5: Run focused tests, then the existing M0 suite.**

Run: `uv run pytest tests/unit/content/test_source.py tests/unit/content/test_profiles.py -v && uv run pytest -m "not live_provider" --ignore=tests/bdd/test_content_intelligence.py -q && make lint`

Expected: focused tests pass and the pre-existing 68 non-live tests remain green.

- [x] **Step 6: Commit the source/profile foundation.**

```bash
git add src/poddown/content tests/unit/content/test_source.py tests/unit/content/test_profiles.py
git commit -m "feat(content): add immutable source and profile contracts"
```

## Task 3: Implement layered pronunciation and critical-token extraction

**Files:**
- Create: `src/poddown/content/lexicon.py`
- Create: `src/poddown/content/tokens.py`
- Create: `tests/unit/content/test_lexicon.py`
- Create: `tests/unit/content/test_tokens.py`

**Interfaces:**
- `LexiconScope = Literal["episode", "project", "domain", "global"]`.
- `PronunciationEntry(entry_id: str, key: str, spoken_form: str, version: str, category: LexiconTokenCategory = "technical_term")` and `PronunciationLexicon(scope: LexiconScope, version: str, entries: tuple[PronunciationEntry, ...])` are frozen. The optional category hint closes the original interface gap without changing the four-argument positional form; entry IDs remain opaque provenance only.
- `PronunciationResolution(normalized_key: str, spoken_form: str, scope: LexiconScope, lexicon_version: str, entry_id: str)` records the selected source.
- `normalize_lexicon_key(value: str) -> str` applies NFC normalization, case-folding, apostrophe normalization, and whitespace collapse.
- `resolve_pronunciation(key: str, layers: Mapping[LexiconScope, PronunciationLexicon]) -> PronunciationResolution | None` checks episode, project, domain, global in that order and raises `LexiconConflictError` when one layer contains multiple entries for the same normalized key.
- `CriticalToken(occurrence_id: str, category: Literal["name", "organization", "product", "acronym", "technical_term", "number", "currency", "percentage", "date", "unit", "ticker", "negation"], normalized: str, source_span: tuple[int, int], script_span: tuple[int, int] | None, expected_spoken_form: str, pronunciation_source: str | None)` is frozen.
- `extract_critical_tokens(text: str, lexicons: Mapping[LexiconScope, PronunciationLexicon] | None = None) -> tuple[CriticalToken, ...]` emits stable occurrence IDs in left-to-right order, preserves repeated occurrences, and assigns expected speech from the layered resolver or deterministic defaults.
- `LexiconTokenCategory` is the shared literal category-hint vocabulary for `name`, `organization`, `product`, and `technical_term`; structural categories always win over lexicon hints. Canonical token spans are half-open UTF-8 byte offsets. For the frozen Task 1 bindings, `resolve_pronunciation` additionally accepts the legacy list-of-entry dictionaries and returns a compatibility result with the legacy selected/accepted/error aliases, while the canonical mapping form remains strict and raises `LexiconConflictError`. The token sequence remains a tuple and exposes read-only `tokens` and deterministic `manifest` aliases for that binding.
- Numeric, date, currency, percentage, and unit tokens use one deterministic verbalizer shared with the M0 fidelity vocabulary; digit-preserving forms are not accepted as the canonical expected speech when a spoken form is required.

- [x] **Step 1: Write unit tests for Unicode/case normalization, layer precedence, same-layer conflicts, all required token categories, repeated numbers and negations, punctuation, spans, and deterministic occurrence IDs.** Include literals such as `SLAM`, `LiDAR`, `C1`, `1.6 Tbit/s`, `21.5 kg`, `12.5%`, `2026-08-09`, `$4.2M`, and `not` twice.
- [x] **Step 2: Run the focused tests and verify the expected red failures.**

Run: `uv run pytest tests/unit/content/test_lexicon.py tests/unit/content/test_tokens.py -v`

Expected: FAIL because lexicon and token extraction behavior is absent.

- [x] **Step 3: Implement exact normalized lexicon resolution.** Do not learn, mutate, or silently select between conflicting entries. Return version and entry provenance for every selected pronunciation.
- [x] **Step 4: Implement deterministic token extraction.** Apply specific patterns before generic patterns so dates, percentages, currencies, numbers with units, acronyms, and ticker-like symbols receive one category each. Use lexicon entries to identify configured names, organizations, products, and technical terms; use explicit `negation` occurrences for every normalized negation token.
- [x] **Step 5: Run focused lexicon/token tests, the green M0 suite, and lint.** The full M1 BDD file remains intentionally red until Task 6 wires the integrated service.

Run: `uv run pytest tests/unit/content/test_lexicon.py tests/unit/content/test_tokens.py -v && uv run pytest -m "not live_provider" --ignore=tests/bdd/test_content_intelligence.py -q && make lint`

Expected: focused lexicon/token tests pass and the pre-existing 68 non-live tests remain green; the full M1 BDD file is intentionally deferred to Task 6 rather than skipped from the final gate.

- [x] **Step 6: Commit the lexicon and token slice.**

```bash
git add src/poddown/content/lexicon.py src/poddown/content/tokens.py tests/unit/content/test_lexicon.py tests/unit/content/test_tokens.py
git commit -m "feat(content): resolve pronunciations and critical tokens"
```

## Task 4: Implement anchored adaptation and bounded turn repair

**Files:**
- Create: `src/poddown/content/adaptation.py`
- Create: `tests/unit/content/test_adaptation.py`

**Interfaces:**
- `AdaptationProposal(treatment: EpisodeTreatment, turns: tuple[ScriptTurn, ...])` is the structured output accepted from a reasoning port.
- `StructuredReasoningPort` exposes `adapt(source: SourceSnapshot, profile: Profile, treatment: EpisodeTreatment) -> AdaptationProposal` and `repair(source: SourceSnapshot, profile: Profile, turn: ScriptTurn, failure: str) -> ScriptTurn`.
- `FixtureReasoningPort(proposals: Mapping[str, AdaptationProposal], repairs: Mapping[str, ScriptTurn])` is deterministic and keyed by source hash and turn ID.
- `adapt_source(source: SourceSnapshot, profile: Profile, treatment: EpisodeTreatment, reasoning: StructuredReasoningPort) -> ScriptVersion` validates all turn IDs, speakers, source anchors, claim anchors, factual claims, target duration, two-speaker structure, and dialogue quality before returning.
- `repair_turn(script: ScriptVersion, turn_id: str, replacement: ScriptTurn, source: SourceSnapshot, profile: Profile) -> ScriptVersion` replaces only the failing turn, preserves all accepted turn IDs and order, and re-validates the replacement.
- `AdaptationError` carries a stable `code` (`unsupported_claim`, `missing_anchor`, `invalid_speaker`, `dialogue_quality`, or `duration`) and safe detail without source leakage.

- [x] **Step 1: Add unit tests for valid anchored proposals, changed numbers, changed negation, unsupported comparisons, missing anchors, invalid speakers, stable IDs, two-speaker disagreement, bounded repair, and zero renderer calls.** Use real fixture proposals and assert structured error codes.
- [x] **Step 2: Run the focused tests and verify the expected red failures.**

Run: `uv run pytest tests/unit/content/test_adaptation.py -v`

Expected: FAIL because adaptation and repair ports are absent.

- [x] **Step 3: Implement the structured reasoning port and fixture adapter.** Keep provider/model payloads out of the public domain models; the fixture adapter is the only default implementation in M1.
- [x] **Step 4: Implement source-bound validation.** Every factual turn must point to an anchor. Extract critical literals from each claim and its anchored source text; reject changed numbers, units, dates, percentages, names, and negation before any rendering path can be called. Editorial turns must not introduce factual claims.
- [x] **Step 5: Implement bounded repair.** Replace only the named failing turn, retain accepted turn IDs and canonical ordering, and fail if the repair changes a protected anchor or introduces a new unsupported claim.
- [x] **Step 6: Run focused adaptation tests, the green M0 suite, and lint.** The full M1 BDD file remains intentionally red until Task 6 wires the integrated service.

Run: `uv run pytest tests/unit/content/test_adaptation.py -v && uv run pytest -m "not live_provider" --ignore=tests/bdd/test_content_intelligence.py -q && make lint`
- [x] **Step 7: Commit the adaptation slice.**

```bash
git add src/poddown/content/adaptation.py tests/unit/content/test_adaptation.py
git commit -m "feat(content): enforce anchored script adaptation"
```

## Task 5: Implement deterministic capability-aware segmentation

**Files:**
- Create: `src/poddown/content/segmentation.py`
- Create: `tests/unit/content/test_segmentation.py`

**Interfaces:**
- `SegmentationCapabilities(max_text_characters: int, max_duration_seconds: float | None, supported_speakers: frozenset[str])` is frozen and validates positive limits.
- `Segment(segment_id: str, turn_ids: tuple[str, ...], speaker_ids: tuple[str, ...], text: str, source_anchors: tuple[SourceAnchor, ...], critical_tokens: tuple[CriticalToken, ...], leading_context: str, trailing_context: str, estimated_duration_seconds: float, difficulty: Literal["normal", "difficult"])` is frozen.
- `SegmentationError` carries stable `code` (`turn_too_large`, `unsupported_speaker`, or `invalid_script`).
- `segment_script(script: ScriptVersion, source: SourceSnapshot, capabilities: SegmentationCapabilities, tokens: tuple[CriticalToken, ...]) -> tuple[Segment, ...]` groups contiguous complete turns, preserves source groupings, never splits a token or turn, and derives stable IDs from canonical turn IDs and boundaries.

- [x] **Step 1: Write unit tests for contiguous grouping, turn order, source grouping, leading/trailing unspoken continuity, capability boundaries, oversized-turn failure, stable IDs, and replay parity.**
- [x] **Step 2: Run the focused tests and verify the expected red failures.**

Run: `uv run pytest tests/unit/content/test_segmentation.py -v`

Expected: FAIL because segmentation behavior is absent.

- [x] **Step 3: Implement the smallest deterministic grouping algorithm.** Add turns until the next complete turn would exceed a declared capability, then start a new segment. If one complete turn exceeds the limit, fail rather than split or truncate it. Continuity context is stored but never included in spoken segment text.
- [x] **Step 4: Run focused segmentation tests, the green M0 suite, and lint.** The full M1 BDD file remains intentionally red until Task 6 wires the integrated service.

Run: `uv run pytest tests/unit/content/test_segmentation.py -v && uv run pytest -m "not live_provider" --ignore=tests/bdd/test_content_intelligence.py -q && make lint`
- [x] **Step 5: Commit the segmentation slice.**

```bash
git add src/poddown/content/segmentation.py tests/unit/content/test_segmentation.py
git commit -m "feat(content): segment canonical scripts deterministically"
```

## Task 6: Integrate the M1 preparation service and replay manifest

**Files:**
- Create: `src/poddown/content/service.py`
- Create: `tests/integration/test_content_pipeline.py`
- Modify: `src/poddown/content/__init__.py`

**Interfaces:**
- `ContentPreparationRequest(markdown: str, profile_yaml: str, treatment: EpisodeTreatment, reasoning: StructuredReasoningPort, lexicon_layers: Mapping[LexiconScope, PronunciationLexicon], capabilities: SegmentationCapabilities, voice_assets: Collection[VoiceAsset], consents: Collection[VoiceConsent])` is immutable.
- `ContentPreparationResult(snapshot: SourceSnapshot, profile: Profile, script: ScriptVersion, tokens: tuple[CriticalToken, ...], segments: tuple[Segment, ...], manifest: Mapping[str, object], manifest_sha256: str)` is immutable.
- `prepare_content(request: ContentPreparationRequest) -> ContentPreparationResult` performs profile validation, source snapshotting, anchored adaptation, lexicon/token resolution, and segmentation in dependency order; it makes no renderer calls.
- The canonical typed API remains authoritative, but `prepare_content` also exposes a narrow compatibility facade for the frozen Task 1 BDD bindings: keyword arguments `source`, `profile`, `proposal`, and `renderer` are accepted and translated into the typed pipeline. The facade returns the canonical result plus read-only aliases `source_snapshot`, `canonical_script`, `critical_tokens`, `segmentation_manifest`, `accepted`, `error`, and `provider_calls`; the renderer argument is a boundary probe and is never called.
- `canonical_manifest(result: ContentPreparationResult) -> Mapping[str, object]` contains only canonical JSON-compatible values, sorted keys, stable ordering, source/profile/script/lexicon versions, anchors, tokens, segments, and capabilities.
- The BDD canonical JSON helper serializes YAML date values through an explicit local default hook; the service must not mutate the process-wide `json.JSONEncoder`.
- Compatibility aliases derive script text and token spans from the validated typed result; frozen BDD expectations must use those canonical spans rather than untrusted proposal wording.
- Compatibility token rows use the typed occurrence ID, category, source form, and spoken form; legacy proposal token rows are selectors only and cannot supply output values.

- [x] **Step 1: Write the integration test for the robotics fixture.** Assert two speakers, target duration between 10 and 15 minutes, source hash, factual anchors, disagreement, token accuracy inputs, segment order, no provider calls, manifest checksum, and equivalent output from two identical requests.
- [x] **Step 2: Run the integration test and verify the expected red failure.**

Run: `uv run pytest tests/integration/test_content_pipeline.py -v`

Expected: FAIL because the orchestration service is absent.

- [x] **Step 3: Implement the orchestration service and public exports.** Keep serialization deterministic with sorted keys and compact separators; never serialize credentials, raw provider IDs, or source text into error messages. Add the narrow Task 1 BDD compatibility facade without weakening the typed request/result contract or invoking its renderer boundary. Use local date serialization in both service and BDD test infrastructure; never install a process-wide JSON encoder hook.
- [x] **Step 4: Run the full M1 BDD/unit/integration surface, then `make check`.**

Run: `uv run pytest tests/bdd/test_content_intelligence.py tests/unit/content tests/integration/test_content_pipeline.py -v && make check`

Expected: all M0 and M1 non-live tests pass with coverage at or above the repository threshold.

- [x] **Step 5: Commit the integrated content-preparation slice.**

```bash
git add src/poddown/content tests/integration/test_content_pipeline.py
git commit -m "feat(content): assemble deterministic preparation manifest"
```

## Task 7: Add adversarial evals, verification evidence, and demo documentation

**Files:**
- Create: `tests/evals/test_content_intelligence.py`
- Create: `tests/fixtures/content/adversarial-adaptation.json`
- Create: `scripts/verify_content_mutation.py`
- Create: `docs/verification/content-intelligence.md`
- Modify: `docs/planning-traceability.md`
- Modify: `docs/product-delivery-plan.md`
- Modify: `pyproject.toml`

**Interfaces:**
- The evals consume only public M1 ports and deterministic fixtures; they do not call live AI or voice providers.
- The verification report records exact commit SHA, commands, test counts, coverage, fixture hashes, and any intentionally deferred risks.

- [x] **Step 1: Write adversarial evals before any documentation claims.** Cover absent-but-plausible claims, contradictions, changed numbers, changed units, changed dates, inserted/removed negation, repeated tokens, code/table blocks, homographs, and source-anchor tampering.
- [x] **Step 2: Run evals and confirm they fail if the corresponding validation is weakened.** Run `PYDANTIC_DISABLE_PLUGINS=1 .venv/bin/python scripts/verify_content_mutation.py`; the isolated copy bypasses the adaptation token-count and orchestration source-token binding gates and must report the changed-number and changed-date evals failing. The implementation branch remains unchanged.
- [x] **Step 3: Implement only missing test fixtures or test utilities needed by the evals.** Do not weaken a gate to make an adversarial case pass.
- [x] **Step 4: Run `make check`, `make build`, and `make docs`; record fresh output and fixture/version IDs in `docs/verification/content-intelligence.md`.**
- [x] **Step 5: Update traceability only for evidence that is actually present.** Mark M1 content-intelligence requirements implemented when BDD, unit, integration, and eval evidence exists; keep audio rendering, Temporal, API, CLI, publishing, MCP, Signal & Supply, and production-readiness rows explicitly pending.
- [x] **Step 6: Commit the M1 evidence.**

```bash
git add tests/evals/test_content_intelligence.py tests/fixtures/content/adversarial-adaptation.json scripts/verify_content_mutation.py docs/verification/content-intelligence.md docs/planning-traceability.md docs/product-delivery-plan.md
git commit -m "test(content): verify M1 adversarial source fidelity"
```

## Plan self-review

- Spec 001 FR-002, FR-003, FR-004, and FR-005 map to Tasks 2–5; FR-006/FR-007 provider rendering and FR-008/FR-009 audio/package behavior remain M2 responsibilities.
- Spec 002 contracts and all seven acceptance behaviors map to Tasks 2–7.
- The roadmap M1 task map is covered in order: BDD, models, source indexing, reasoning port, adaptation/repair, lexicons, extraction, segmentation, adversarial evals, and verification.
- The demo source contains the required technical names, acronyms, dates, percentages, numbers/units, repeated critical tokens, negation, and disagreement; rendering/mastering remains intentionally deferred to M2.
- Every production file has one responsibility, every task has a disjoint write set, and all public interfaces used by later tasks are defined here.
- No placeholder, TODO, fake live-provider claim, unbounded retry, automatic lexicon mutation, or unresolved product choice is introduced by this plan.

Execution starts immediately with Task 1 under `superpowers:subagent-driven-development`; the coordinating agent will review every task before dispatching the next one.
