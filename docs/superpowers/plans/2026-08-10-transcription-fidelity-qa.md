# Transcription and fidelity QA milestone

**Date:** 2026-08-10
**Branch:** `codex/m2-transcription-fidelity`
**Parent:** `codex/m2-provider-temporal-render` / PR #8
**Specifications:** `specs/001-core-audio-vertical-slice/spec.md` FR-006--FR-008; `specs/003-durable-audio-production/spec.md` workflow contract and acceptance 4--7.

## Outcome

Make transcription a real, injectable input to rendered-candidate QA. A
provider-neutral transcriber returns normalized text and provenance; the
activity validates persisted audio first, transcribes second, evaluates the
canonical critical-token gate, and persists immutable transcription evidence.
Deterministic local mode remains available and is labeled as an offline fixture.
Mastering, final-master transcription, packaging, and live-provider evidence
remain intentionally deferred to the next milestones.

## Contract decision

The approved provider contract currently describes a path-based `Transcriber`
while the existing adapter and durable activity boundary operate on verified
audio bytes. The implementation contract will be reconciled to the byte-based
boundary because the activity owns artifact validation and must not expose
provider-specific filesystem paths. The normalized result will retain text,
word timestamps when available, provider/model/request ID, response checksum,
usage, and an explicit decimal cost.

## Acceptance behaviors

1. A provider-backed candidate is diagnosed before transcription, and its
   normalized transcript is passed to deterministic critical-token fidelity QA.
2. A matching transcript produces a hard-gated candidate with transcript
   provenance, usage, checksum, and estimated cost that round-trips through the
   Temporal payload and an independent durable transcription cost event.
3. A missing or changed critical token produces `accuracy < 1.0` and
   `rerender_scope="segment"`; soft score cannot select it.
4. A malformed/non-retryable transcription response fails closed with a stable
   transcription error and never falls back to canonical expected text.
5. A retryable transcription failure remains retryable under Temporal's bounded
   policy and reports a transcription gate after exhaustion.
6. Replaying an activity after the atomic transcription response/cost record is
   durably saved returns the saved quality result without a second
   transcription or renderer dispatch or cost record.
7. Deterministic local mode remains explicit, deterministic, and zero-cost; it
   is not represented as live provider evidence.

## Implementation sequence (BDD then TDD)

### Task 1 — Reconcile normalized transcription contracts

Files: `specs/001-core-audio-vertical-slice/contracts/provider.py`,
`src/poddown/providers/contracts.py`,
`src/poddown/providers/openai_transcription.py`, and provider contract tests.

- [x] Add failing contract tests for the byte-based protocol and normalized
  result cost/provider fields.
- [x] Confirm the current adapter contract tests fail for the missing fields.
- [x] Move/re-export normalized transcript values from the OpenAI module into
  the provider-neutral contract and preserve compatibility imports.
- [x] Add explicit cost-estimator injection with deterministic zero-cost default
  for existing offline fixtures; never call a live provider in local tests.
- [x] Verify malformed responses and invalid transcript metadata fail closed.

### Task 2 — Persist transcription evidence with candidate quality

Files: `src/poddown/audio/selection.py`, `src/poddown/audio/storage.py`, and
focused unit/integration tests.

- [x] Add failing serialization tests for immutable transcription evidence.
- [x] Implement normalized transcription evidence and optional backwards-compatible
  `CandidateQuality.transcription` evidence.
- [x] Add an atomic `FilesystemQualityRecordStore` keyed by candidate ID and
  reject conflicting evidence.
- [x] Add an atomic `FilesystemTranscriptionRecordStore` containing the
  normalized response and independent estimated-cost event; reject conflicts.
- [x] Verify checksum, usage, cost, and provider identity survive both
  round-trips.

### Task 3 — Inject transcription into the Temporal activity

Files: `src/poddown/audio/activities.py`, `src/poddown/audio/workflow.py`,
`src/poddown/audio/__init__.py`, BDD feature/steps, and activity tests.

- [x] Add BDD scenarios for pass, mismatch, provider failure, replay, and
  explicit deterministic-local mode; observe the expected RED failures.
- [x] Implement an async-capable quality evaluator and injected `Transcriber`
  factory. Validate audio before dispatch, evaluate returned transcript text,
  persist the transcription response/cost event before quality evaluation, and
  persist quality evidence after successful evaluation.
- [x] Map malformed/non-retryable and retryable transcription failures to
  stable Temporal error codes and the correct `transcription` gate.
- [x] Preserve candidate selection and segment-only repair semantics.
- [x] Verify a passing sibling take remains selectable when another take's
  transcription fails.
- [x] Verify a quality-write failure replays the atomic transcription record
  without a second provider dispatch.

### Task 4 — Documentation and verification evidence

Files: `docs/verification/transcription-fidelity-qa.md`,
`docs/planning-traceability.md`, and this plan.

- [x] Run focused BDD, unit, integration, provider contract, branch-aware
  coverage, Ruff, strict mypy, build, docs, schema, dependency, security, and
  clean-diff checks applicable to the changed scope.
- [x] Record deterministic-local versus provider-backed evidence explicitly;
  do not claim live-provider fidelity without opt-in credentials and a live
  run.
- [x] Update traceability for transcription/fidelity QA while leaving mastering,
  final-master QA, packaging, and publication marked pending.
- [x] Request independent review, address every actionable finding, re-run the
  verification gate, and publish a ready PR stacked on PR #8.

The transcription record stores provider-reported or injected estimated cost
and request identity atomically with the normalized response. External billing
reconciliation against provider invoices remains owned by the M3 metering
milestone; this slice does not claim exactly-once external billing when a worker
dies between provider response and local record commit.

## Verification gate

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
uv run pytest -p pytest_bdd.plugin <focused BDD/unit/integration/contract tests> -q
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 make check
uv run ruff format --check <changed Python files>
uv run ruff check <changed Python files>
uv run mypy src/poddown/audio src/poddown/providers
make build
make docs
uv lock --check
uv pip check
```

`make check-full` and `make quality-gates` remain CI-owned and will not be run
locally.
