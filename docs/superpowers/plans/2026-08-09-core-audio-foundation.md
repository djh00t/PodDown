# Core Audio Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the tested foundation that accepts canonical Markdown, enforces voice rights, selects provider adapters without weakening gates, and verifies critical-token fidelity.

**Architecture:** Build a small `src/poddown` package with immutable domain values and application functions. Provider SDKs remain outside this slice; deterministic in-memory adapters establish and test the ports before ElevenLabs/OpenAI HTTP integration. Each Gherkin scenario must be observed failing before its minimum implementation is added.

**Tech Stack:** Python 3.12, uv, pytest 8, pytest-bdd 8, pytest-cov 6, PyYAML, Pydantic 2

## Global Constraints

- Markdown remains canonical and unknown keys inside `poddown` are rejected.
- No provider call occurs before validation and rights checks pass.
- Critical factual tokens require accuracy exactly `1.0`.
- Provider fallback creates a distinct candidate and retains every hard gate.
- Provider SDK types do not cross the adapter boundary.
- Core functionality maintains at least 80% branch and line coverage.
- No live provider test runs by default or on an untrusted pull request.

---

### Task 1: Package and immutable domain contracts

**Files:**
- Modify: `pyproject.toml`
- Create: `src/poddown/__init__.py`
- Create: `src/poddown/domain.py`
- Test: `tests/unit/test_domain.py`

**Interfaces:**
- Produces: `SourceValidation`, `FidelityResult`, `CandidateResult`, and `ProviderUsage` frozen dataclasses.

- [ ] **Step 1: Add a unit test that attempts to mutate each result and expects `FrozenInstanceError`.**
- [ ] **Step 2: Run `uv run pytest tests/unit/test_domain.py -v`; confirm failure because `poddown.domain` is absent.**
- [ ] **Step 3: Add `pyyaml>=6.0.2,<7` and `pydantic>=2.11,<3`, configure Hatch for `src/poddown`, and implement:**

```python
@dataclass(frozen=True)
class ProviderUsage:
    input_units: int
    output_units: int


@dataclass(frozen=True)
class SourceValidation:
    accepted: bool
    source_sha256: str | None
    errors: tuple[str, ...]
    provider_calls: int = 0


@dataclass(frozen=True)
class FidelityResult:
    passed: bool
    accuracy: float
    rerender_scope: Literal["none", "segment"]


@dataclass(frozen=True)
class CandidateResult:
    accepted: bool
    provider_calls: int
    provider: str | None = None
    candidate_id: str | None = None
    submitted_text: str | None = None
    request_id: str | None = None
    model: str | None = None
    usage: ProviderUsage | None = None
    cost: Decimal = Decimal("0")
    checksum: str | None = None
    required_gates: frozenset[str] = frozenset()
```

- [ ] **Step 4: Run the unit test and the three BDD modules; confirm the unit test passes while BDD remains red for missing behavior.**
- [ ] **Step 5: Commit with `feat: define immutable audio foundation contracts`.**

### Task 2: Markdown intake

**Files:**
- Create: `src/poddown/intake.py`
- Modify: `tests/bdd/test_intake.py`
- Test: `tests/unit/test_intake.py`

**Interfaces:**
- Consumes: `SourceValidation`.
- Produces: `validate_markdown(source: str, available_profiles: Collection[str]) -> SourceValidation`.

- [ ] **Step 1: Run each intake scenario separately and record the expected missing-module failure. Add unit cases for malformed YAML, non-object `poddown`, missing profile, unknown profile, unknown PodDown key, and source hash stability.**
- [ ] **Step 2: Run `uv run pytest tests/unit/test_intake.py tests/bdd/test_intake.py -v`; confirm all cases fail for missing intake behavior.**
- [ ] **Step 3: Implement strict frontmatter extraction, allowed-key validation against `markdown-frontmatter.schema.json`, profile resolution, and SHA-256 over the exact UTF-8 source. Return structured failures rather than calling a provider or rewriting input.**
- [ ] **Step 4: Run focused intake tests, then `uv run pytest tests -v`; confirm intake is green and untouched scenarios remain red only for their missing modules.**
- [ ] **Step 5: Commit with `feat: validate canonical markdown intake`.**

### Task 3: Critical-token fidelity gate

**Files:**
- Create: `src/poddown/qa/__init__.py`
- Create: `src/poddown/qa/fidelity.py`
- Modify: `tests/bdd/test_fidelity_qa.py`
- Test: `tests/unit/qa/test_fidelity.py`

**Interfaces:**
- Consumes: canonical expected-spoken token strings and normalized transcript text.
- Produces: `evaluate_critical_tokens(expected: tuple[str, ...], transcript: str) -> FidelityResult`.

- [ ] **Step 1: Preserve the existing Gherkin examples and add unit cases for Unicode/case normalization, repeated tokens, punctuation, empty expected tokens, insertions or losses of negation, and partial matches.**
- [ ] **Step 2: Run `uv run pytest tests/unit/qa/test_fidelity.py tests/bdd/test_fidelity_qa.py -v`; confirm failure because fidelity behavior is absent.**
- [ ] **Step 3: Implement deterministic normalization and exact phrase occurrence matching. Accuracy is matched expected occurrences divided by expected occurrences; any missing token or negation returns `passed=False` and `rerender_scope="segment"`; an empty expected set returns `1.0`.**
- [ ] **Step 4: Run focused fidelity tests, then the suite; confirm fidelity is green without changing audio soft-gate behavior.**
- [ ] **Step 5: Commit with `feat: enforce exact critical-token fidelity`.**

### Task 4: Provider capability and rights policy

**Files:**
- Create: `src/poddown/providers/__init__.py`
- Create: `src/poddown/providers/contracts.py`
- Create: `src/poddown/rendering.py`
- Modify: `tests/bdd/test_provider_rendering.py`
- Test: `tests/unit/test_rendering.py`

**Interfaces:**
- Produces: `ProviderCapabilities`, `VoiceRenderer` protocol, and `request_candidate(text: str, rights_valid: bool, provider: str, eligible: Collection[str], unavailable: Collection[str], renderer: VoiceRenderer | None = None) -> CandidateResult`.

- [ ] **Step 1: Add unit tests for revoked rights, ineligible provider, unsupported capability, transient unavailability, immutable submitted text, distinct IDs, complete metering, and unchanged hard gates.**
- [ ] **Step 2: Run provider unit and BDD tests; confirm failures occur before implementation.**
- [ ] **Step 3: Define capabilities for formats, sample rates, text limits, model/voice pinning, timestamps, and provider idempotency. Define an async renderer port returning normalized candidate metadata. Implement policy ordering: rights, eligibility, availability, capability, dispatch, metadata validation. Use a deterministic test renderer; do not add vendor SDKs.**
- [ ] **Step 4: Run focused provider tests and the full suite. Verify that a revoked voice records zero calls and fallback retains `frozenset({"critical_tokens", "audio_quality"})`.**
- [ ] **Step 5: Commit with `feat: enforce provider rendering policy`.**

### Task 5: ElevenLabs and OpenAI adapter contract tests

**Files:**
- Create: `src/poddown/providers/elevenlabs.py`
- Create: `src/poddown/providers/openai_audio.py`
- Create: `src/poddown/providers/openai_transcription.py`
- Create: `tests/contract/providers/test_elevenlabs.py`
- Create: `tests/contract/providers/test_openai_audio.py`
- Create: `tests/contract/providers/test_openai_transcription.py`
- Create: `tests/fixtures/providers/*.json`

**Interfaces:**
- Consumes: provider-neutral ports and injected async HTTP transports.
- Produces: adapters that map sanitized HTTP fixtures to domain results without importing vendor types into application code.

- [ ] **Step 1: Write contract tests for request shape, immutable text, explicit model/voice, idempotency header where supported, timeout/rate-limit classification, malformed audio rejection, usage/cost normalization, response checksum, and credential redaction.**
- [ ] **Step 2: Run `uv run pytest tests/contract/providers -v`; confirm adapter imports fail.**
- [ ] **Step 3: Implement minimal adapters using injected transports. Keep authentication in adapter settings, map responses into normalized contracts, and make SDK/HTTP retry counts explicit and bounded.**
- [ ] **Step 4: Run contract tests, then the complete non-live suite. Confirm no network calls occur.**
- [ ] **Step 5: Commit with `feat: add voice and transcription adapters`.**

### Task 6: Quality gates and implementation-readiness verification

**Files:**
- Create: `Makefile`
- Create: `.github/workflows/ci.yaml`
- Create: `tests/evals/test_provider_policy.py`
- Modify: `docs/provider-guidelines.md`

**Interfaces:**
- Consumes: all preceding public interfaces.
- Produces: repeatable local/CI commands and executable provider-policy evals.

- [ ] **Step 1: Add evals proving rights precede cost, provider agreement cannot waive critical tokens, fallback preserves gates, provider text is unchanged, and live tests require both marker and environment opt-in.**
- [ ] **Step 2: Run evals before CI configuration and confirm any uncovered policy fails.**
- [ ] **Step 3: Add `clean`, `install`, `build`, `test`, `lint`, and `docs` targets. Configure CI for lockfile sync, lint/type checks, BDD/unit/contract/eval tests, and 80% branch/line coverage; exclude `live_provider`.**
- [ ] **Step 4: Run `make lint`, `make test`, and `make build`; inspect complete output and fix only defects within this slice.**
- [ ] **Step 5: Re-read FR-001, FR-004, FR-006, FR-007, provider guidelines, and every Gherkin scenario; map each to a passing test in the verification report.**
- [ ] **Step 6: Commit with `ci: enforce bdd and provider quality gates`.**

## Deferred after this foundation

The next plans cover source-bound script adaptation, pronunciation and critical-token extraction, semantic segmentation, Temporal candidate/rerender orchestration, deterministic mastering/final QA, and immutable episode packaging. Each begins with its own reviewed Gherkin scenarios before production changes.
