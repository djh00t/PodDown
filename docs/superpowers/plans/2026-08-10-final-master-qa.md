# Final-master transcription and fidelity QA

**Date:** 2026-08-10
**Branch:** `codex/m2-final-master-qa`
**Parent:** `codex/m2-mastering-package` / PR #10
**Specifications:** `specs/001-core-audio-vertical-slice/spec.md` FR-008; `specs/003-durable-audio-production/spec.md` final-master QA contract.

## Outcome

Add an independent final-master QA boundary over the verified mastered WAV.
The service rechecks the exact master bytes against the versioned mastering
profile, transcribes those bytes through the existing provider-neutral
`Transcriber` port, binds the response checksum to the master, evaluates all
critical-token occurrences, and emits a schema-compatible `stage=master` QA
record with provider usage and cost available for the package milestone.

This slice does not assemble the immutable nine-file package, publish media,
or rerender segments. A failed final-master fidelity result is returned as a
failed QA record so the following package boundary can block publication;
provider and malformed-master failures raise stable terminal/transient errors.

## Contract decisions

- Final QA accepts `MasteredAudio`, `MasteringProfile`, and the complete
  critical-token tuple; it does not rediscover source or candidate state.
- Final QA uses the exact mastered WAV bytes, not a re-encoded or reconstructed
  representation. The transcript checksum must equal those bytes.
- The mastering profile version in the request must match the mastered
  provenance profile version before transcription dispatch.
- Audio and duration gates are checked before transcription. A malformed or
  out-of-profile master fails closed without provider dispatch.
- `FinalMasterQaResult.to_dict()` is limited to the existing QA schema fields;
  transcript, checksum, usage, and cost remain typed result evidence for the
  package/provenance milestone.
- Deterministic tests inject a fake transcriber. No live provider call or
  credential is required.

## Acceptance behaviors (BDD first)

1. A valid mastered WAV is transcribed exactly once and produces a passing
   `stage=master` QA record with 100% critical-token accuracy and bound checksum.
2. A final transcript missing a critical token returns a failed QA record and
   never reports package-ready success.
3. A transcript checksum for different audio fails before fidelity evaluation.
4. A malformed, wrong-rate, clipped, or out-of-duration master fails before
   transcription dispatch.
5. Provider timeout/rate-limit errors map to retryable transcription errors;
   malformed or terminal provider evidence maps to a non-retryable error.
6. Equivalent mastered bytes and profile values produce stable QA serialization
   and checksum evidence.

## Implementation sequence

### Task 1 — Define executable acceptance and immutable QA contracts

Files: `tests/features/final_master_qa.feature`,
`tests/bdd/test_final_master_qa.py`, `tests/unit/qa/test_final_master.py`,
`src/poddown/qa/final_master.py`.

- [x] Add BDD scenarios for passing QA, failed token fidelity, checksum mismatch,
  pre-dispatch media failure, provider failure mapping, and stable evidence.
- [x] Observe the scenarios fail because the final-master QA boundary is absent.
- [x] Add immutable gate/result contracts and schema-compatible serialization.

### Task 2 — Implement final-master transcription and gates

Files: `src/poddown/qa/final_master.py`, `src/poddown/qa/__init__.py`,
`tests/unit/qa/test_final_master.py`.

- [x] Recheck exact mastered WAV bytes against the profile before dispatch.
- [x] Invoke the existing asynchronous `Transcriber` port and map failures
  without exposing provider details or weakening checksum/fidelity gates.
- [x] Return typed transcript, provider usage/cost, checksum, gate, and score
  evidence with deterministic serialization.

### Task 3 — Regression, evidence, and handoff

Files: `docs/verification/final-master-qa.md`,
`docs/planning-traceability.md`, and this plan.

- [x] Run BDD, focused unit/integration tests, existing audio regressions,
  branch-aware coverage, Ruff, strict mypy, build, docs, schema, dependency,
  security, and clean-diff checks.
- [x] Validate the serialized QA record against `qa.schema.json` and document
  deterministic-local versus provider-contract evidence.
- [ ] Request independent Luna/Terra review, address valid findings, rerun the
  verification gate, and publish a normal ready PR stacked on PR #10.

`make check-full` and `make quality-gates` remain CI-owned and will not be run
locally.
