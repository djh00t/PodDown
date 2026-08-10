# Deterministic mastering and media inspection

**Date:** 2026-08-10
**Branch:** `codex/m2-mastering-package`
**Parent:** `codex/m2-transcription-fidelity` / PR #9
**Specifications:** `specs/001-core-audio-vertical-slice/spec.md` FR-008; `specs/003-durable-audio-production/spec.md` audio diagnostics and mastering contract.

## Outcome

Add a provider-neutral deterministic mastering boundary for accepted PCM WAV
segments. A pinned profile validates inputs, assembles segments in canonical
order with deterministic silence/crossfade settings, invokes an injected ffmpeg
runner, verifies WAV/MP3 outputs, and records command/version/filter and
checksum provenance. This slice does not claim final-master transcription,
immutable package assembly, publication, or subjective listening calibration;
those are the next dependency-safe slices.

## Contract decisions

- Mastering receives verified `RenderedAudio` bytes plus stable segment IDs and
  does not read provider paths or mutate candidate records.
- The default runner invokes the locally resolved `ffmpeg` executable; tests
  inject a deterministic runner so CI does not depend on provider services.
- The profile pins sample rate, mono output, silence/crossfade durations, output
  formats, duration bounds, peak/clipping limits, and a versioned profile ID.
- Input audio is diagnosed before ffmpeg dispatch. Any malformed or out-of-bound
  segment fails closed without producing a mastered result.
- Output bytes are verified with the existing WAV diagnostics and ffprobe-backed
  media inspection; provenance records estimated command/version and all input
  and output checksums.

## Acceptance behaviors (BDD first)

1. Accepted segments are assembled in stable segment order with pinned profile
   settings and produce deterministic WAV and MP3 outputs.
2. Malformed, truncated, wrong-rate, wrong-channel, clipped, or out-of-duration
   input fails before the ffmpeg runner is dispatched.
3. A runner failure is surfaced as a stable mastering error without a partial
   success result.
4. Mastered WAV/MP3 metadata satisfies the profile and output checksums remain
   stable for equivalent input bytes and profile values.
5. Provenance contains profile version, ffmpeg executable/version, command,
   filters, ordered input checksums, output checksums, and media metadata.

## Implementation sequence

### Task 1 — Define executable acceptance and public contracts

Files: `tests/features/mastering.feature`, `tests/bdd/test_mastering.py`,
`tests/unit/audio/test_mastering.py`, `src/poddown/audio/mastering.py`.

- [x] Add BDD scenarios for deterministic success, pre-dispatch input failure,
  runner failure, and stable provenance/checksums.
- [x] Observe the new scenarios fail for the missing mastering boundary.
- [x] Add frozen `MasteringProfile`, `MasteringSegment`, `MasteredAudio`, and
  `MasteringProvenance` values plus stable error types.

### Task 2 — Implement the ffmpeg-backed mastering boundary

Files: `src/poddown/audio/mastering.py`, `src/poddown/audio/__init__.py`,
`tests/unit/audio/test_mastering.py`, and media fixtures.

- [x] Implement deterministic input ordering, WAV preflight, and bounded profile
  validation before external dispatch.
- [x] Implement an injectable runner protocol and a subprocess ffmpeg adapter
  with redacted, bounded errors and pinned command construction.
- [x] Verify WAV and MP3 outputs, checksums, media metadata, and provenance.
- [x] Keep local tests deterministic and offline by using a fake runner plus a
  small real-ffmpeg smoke test when the executable is available.

### Task 3 — Regression and evidence

Files: `docs/verification/deterministic-mastering.md`,
`docs/planning-traceability.md`, and this plan.

- [x] Run BDD, focused unit/integration tests, existing audio regressions,
  branch-aware coverage, Ruff, strict mypy, build, docs, schema, dependency,
  security, and clean-diff checks.
- [x] Record deterministic-local versus real-ffmpeg evidence and the exact
  executable/version used; do not claim final package or final-master QA.
- [x] Request independent Luna/Terra review, address every valid finding, and
  rerun the verification gate.
- [ ] Publish a normal ready PR stacked on PR #9.

`make check-full` and `make quality-gates` remain CI-owned and will not be run
locally.
