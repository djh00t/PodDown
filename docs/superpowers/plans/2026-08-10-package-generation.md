# Deterministic package artifact generation

**Date:** 2026-08-10
**Branch:** `codex/m2-package-generation`
**Parent:** `codex/m2-immutable-package` / PR #13
**Specifications:** `specs/001-core-audio-vertical-slice/spec.md` FR-009; `specs/003-durable-audio-production/spec.md` package/final-QA contract.

## Outcome

Add a pure, deterministic builder that converts verified MasteredAudio, FinalMasterQaResult, and immutable content/workflow evidence into the nine PackageArtifact values consumed by EpisodePackageService. The builder owns transcript text/VTT, chapters, source-anchored show notes, QA report, provenance, and render manifest serialization; it does not write storage, call providers, mutate source/script state, or publish.

## Contract decisions

- PackageGenerationInput carries episode UUID, SourceSnapshot, Profile, ScriptVersion, positive numeric script version, ordered Segment values, canonical content manifest, MasteredAudio, passing FinalMasterQaResult, render evidence, renderer identity, and optional lexicon evidence.
- episode.wav and episode.mp3 use the exact bytes from MasteredAudio; their checksums must match MasteringProvenance.
- transcript.txt uses the exact final transcript text plus one trailing newline.
- transcript.vtt requires non-empty, monotonic TranscriptWord timestamps and emits deterministic one-word cues with stable three-decimal timestamps. Missing or invalid timestamps fail closed.
- chapters.json derives ordered segment chapters from segment estimated durations and stable segment IDs; cumulative offsets are deterministic and never invented from provider timing.
- show-notes.md contains only source-anchored deterministic metadata/excerpts supplied in the input; no unsupported factual claims are generated.
- qa-report.json uses FinalMasterQaResult.to_dict() and must serialize compactly with sorted keys.
- provenance.json records source/script/profile/lexicon/renderer/mastering/workflow/QA/usage/cost/final-checksum evidence through JSON-serializable details.
- render-manifest.json records the canonical content manifest, ordered segment identities, render evidence, and exact master checksums using compact sorted JSON.
- Any failed final QA, inconsistent master checksum, missing source/script/segment identity, non-JSON evidence, or malformed timestamp blocks artifact generation before package commit.

## Acceptance behaviors (BDD first)

1. Passing final-master QA produces all nine artifacts with deterministic bytes and exact audio checksums.
2. Transcript text and VTT are reproducible; VTT timestamps preserve provider word evidence and reject missing/invalid timing.
3. Chapters preserve segment order with deterministic cumulative start/end times.
4. Show notes preserve supplied source-anchored content without adding claims.
5. QA, provenance, and render manifest include the required evidence and serialize deterministically.
6. Failed final-master QA, checksum mismatch, missing content identity, or non-serializable evidence prevents generation and storage dispatch.
7. Equivalent immutable inputs produce byte-identical artifact payloads and replay through EpisodePackageService returns the same package.

## Implementation sequence

### Task 1 — Define executable generation behavior

Files: `tests/features/package_generation.feature`, `tests/bdd/test_package_generation.py`, `tests/unit/test_package_generation.py`.

- [x] Add BDD scenarios for complete generation, VTT/chapter determinism, provenance/manifest evidence, failure gates, and equivalent replay.
- [x] Observe the new scenarios fail because the generation boundary is absent.

### Task 2 — Implement pure package artifact builders

Files: `src/poddown/package_generation.py`.

- [x] Add immutable PackageGenerationInput validation and stable helper serialization.
- [x] Generate the seven non-audio artifacts with explicit timestamp and source-evidence gates.
- [x] Preserve exact MasteredAudio bytes and bind final QA/provenance checksums before returning artifacts.
- [x] Keep all storage/provider side effects outside the builder.

### Task 3 — Evidence and handoff

Files: `docs/verification/package-generation.md`, `docs/planning-traceability.md`, and this plan.

- [x] Run BDD, unit, failure-path, existing package/audio regression, schema-constraint, branch-aware coverage, Ruff, strict mypy, build, docs, dependency, security, and clean-diff checks.
- [x] Validate generated package artifacts and a committed manifest against the approved schema constraints.
- [x] Request independent Luna/Terra review, address valid findings, and rerun the verification gate.
- [ ] Publish a normal ready PR stacked on PR #13.

`make check-full` and `make quality-gates` remain CI-owned and will not be run locally.
