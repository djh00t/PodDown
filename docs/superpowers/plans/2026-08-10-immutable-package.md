# Immutable artifact storage and episode-package assembly

**Date:** 2026-08-10
**Branch:** `codex/m2-immutable-package`
**Parent:** `codex/m2-final-master-qa` / PR #12
**Specifications:** `specs/001-core-audio-vertical-slice/spec.md` FR-009; `specs/003-durable-audio-production/spec.md` workflow/package contract.

## Outcome

Add the smallest immutable package boundary that can consume verified master and QA evidence. Raw artifact bytes are stored content-addressably, package manifests are immutable and replay-safe, and the manifest serializer satisfies the approved episode-package schema without exposing storage implementation details.

This slice does not add Temporal orchestration, publication adapters, provider billing reconciliation, or a UI. It provides the storage/package contracts those later milestones depend on.

## Contract decisions

- Artifact content identity is the lowercase SHA-256 of exact bytes; an existing digest is never overwritten.
- Artifact metadata includes package name, media type, byte count, checksum, and a storage key; manifest serialization emits only the schema fields.
- The package must contain the nine approved members: episode.wav, episode.mp3, transcript.txt, transcript.vtt, chapters.json, show-notes.md, qa-report.json, provenance.json, and render-manifest.json.
- Package files are sorted by the approved member order for deterministic serialization; duplicate names, missing required names, unsafe names, and mismatched metadata fail closed.
- Provenance requires a valid source checksum, positive script version, non-empty profile/renderer, qa=pass, critical-token accuracy=1.0, and final_sha256 equal to episode.wav.
- A package commit writes a manifest atomically under an episode-version UUID. Repeating an identical commit returns the stored manifest; a conflicting commit raises a terminal conflict.
- Deterministic tests use temporary filesystem roots and no external services.

## Acceptance behaviors (BDD first)

1. A complete QA-passed artifact set creates a nine-file manifest with exact byte counts and SHA-256 checksums.
2. Missing required artifacts, duplicate names, unsafe names, failed QA, sub-100% critical-token accuracy, invalid provenance, or a final checksum mismatch block package commit.
3. Content-addressed storage returns the same reference for repeated identical bytes and rejects corrupted stored bytes.
4. Replaying an identical package commit returns the original immutable manifest without overwriting it.
5. A conflicting package commit for an existing episode version fails without replacing the original manifest.
6. Manifest serialization contains only approved top-level and file fields and validates against the episode-package schema constraints.

## Implementation sequence

### Task 1 — Define executable package behavior

Files: `tests/features/package.feature`, `tests/bdd/test_package.py`, `tests/unit/test_artifacts.py`, `tests/unit/test_packages.py`.

- [x] Add BDD scenarios for complete package commit, hard-gate rejection, replay, conflict, and schema-safe serialization.
- [x] Observe the new scenarios fail because artifact/package contracts are absent.

### Task 2 — Implement immutable artifacts and package commit

Files: `src/poddown/artifacts.py`, `src/poddown/packages.py`.

- [x] Add immutable ArtifactRef and an injected ArtifactStore protocol.
- [x] Implement atomic content-addressed filesystem storage with read-time checksum verification.
- [x] Add EpisodePackage, PackageArtifact, PackageProvenance, and an immutable filesystem-backed package commit service.
- [x] Enforce required package names, exact hashes/byte counts, QA hard gates, UUID identity, and deterministic schema-compatible serialization.

### Task 3 — Evidence and handoff

Files: `docs/verification/immutable-package.md`, `docs/planning-traceability.md`, and this plan.

- [x] Run BDD, unit, storage failure-path, schema-constraint, existing regression, branch-aware coverage, Ruff, strict mypy, build, docs, dependency, security, and clean-diff checks.
- [x] Validate a real manifest against the approved episode-package schema constraints.
- [x] Request independent Luna/Terra review, address valid findings, rerun the verification gate, and publish a normal ready PR stacked on PR #12.

`make check-full` and `make quality-gates` remain CI-owned and will not be run locally.
