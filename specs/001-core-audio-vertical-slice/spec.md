# Specification: Core Audio Vertical Slice

**Status:** Approved for planning
**Date:** 2026-08-09

## Goal

Produce a reproducible 10–15 minute, two-speaker episode package from a difficult
non-finance technical Markdown document, with automatic segment repair and no
unsupported factual claims.

## Scope

This slice includes local Markdown/profile validation, source snapshotting,
source-bound script adaptation, pronunciation resolution, critical-token
extraction, semantic segmentation, provider-neutral rendering, multiple candidate
takes, transcription and audio QA, segment rerendering, deterministic mastering,
final verification, provenance, and cost capture.

It excludes publishing, MCP, GitHub Actions, billing, teams, UI, Kubernetes, and
automatic lexicon mutation.

## Functional requirements

### FR-001 Markdown contract

Input MUST be UTF-8 CommonMark-compatible Markdown. YAML frontmatter is optional.
If present, PodDown reads only the `poddown` object:

```yaml
poddown:
  profile: technical-dialogue
  format: dialogue
  target_minutes: 12
  pronunciation_overrides:
    C1: "see one"
```

Unknown `poddown` keys fail validation. Other frontmatter keys are preserved and
ignored. `profile` is required after CLI/default resolution. Document values may
override only profile fields explicitly marked document-overridable. The source
body and complete frontmatter are hashed without rewriting the file.

### FR-002 Profile contract

Profiles are versioned YAML documents containing `show`, `format`, `speakers`,
`style`, `audio`, `quality`, and optional `publishing`. Speaker references must
resolve to active voice assets with valid consent. `format.type` is `narration` or
`dialogue`; dialogue requires at least two speakers. Target duration must be from
1 to 180 minutes.

### FR-003 Canonical script

Adaptation MUST return structured turns with stable IDs, speaker IDs, text,
source anchors, and claim anchors. Every factual sentence must cite at least one
source anchor. Dialogue devices without factual content may be marked editorial.
The canonical script is immutable and versioned before rendering.

### FR-004 Pronunciation and critical tokens

Resolution order is episode override, project lexicon, domain lexicon, then global
lexicon. The highest-priority exact normalized key wins. The resolved lexicon and
its component versions are recorded. Names, organizations, numbers, percentages,
units, dates, acronyms, technical terms, ticker-like symbols, and negation MUST be
extracted as critical tokens with expected spoken forms.

### FR-005 Segmentation

Segments contain contiguous turns, preserve order and source grouping, and stay
inside renderer capabilities. A critical token may not be split. Each segment
records preceding and following continuity context that is not spoken.

### FR-006 Rendering and candidate selection

Rendering occurs only after rights validation. Each segment receives at least one
candidate and difficult or failed segments receive up to three candidates per
attempt. Candidates are scored for transcript fidelity, critical tokens,
pronunciation, silence, clipping, speech rate, cadence, consistency, artifacts,
and repetition. Only candidates with all hard gates passing are selectable.

Renderer adapters MUST expose capabilities and normalized usage/cost metadata.
ElevenLabs is the initial primary renderer and OpenAI Audio is the initial
fallback, but profiles contain only portable policy plus provider/voice
references. Neither provider may generate, rewrite, truncate, or independently
select script content. A provider change creates a distinct candidate with its
own request ID, checksum, QA, usage, cost, and provenance.

### FR-007 QA and repair

Each candidate is transcribed and compared with the canonical segment. Critical
token accuracy MUST equal 1.0. Loss or insertion of negation is a critical failure.
Configurable soft thresholds rank candidates but cannot waive hard gates. If no
candidate passes, only that segment is rerendered, up to the profile's bounded
attempt count. Exhaustion fails the render job with structured evidence.

### FR-008 Mastering and final QA

Accepted PCM segments are stitched with deterministic silence and crossfade rules,
then processed with the versioned spoken-word mastering profile. WAV and MP3
outputs meet the profile loudness, true-peak, sample-rate, and channel constraints.
The final master is transcribed and must pass the same critical-token gate before
the package succeeds.

### FR-009 Package and provenance

The immutable package contains `episode.wav`, `episode.mp3`, `transcript.txt`,
`transcript.vtt`, `chapters.json`, `show-notes.md`, `qa-report.json`,
`provenance.json`, and `render-manifest.json`. Every file has a SHA-256 checksum.
Provenance identifies source, script, profile, lexicons, provider models, voice
assets, mastering profile, workflow/job, QA results, usage, costs, and final hash.

## Acceptance scenarios

1. Valid Markdown plus profile produces a fully verified episode package.
2. An unsupported factual sentence in adaptation fails before rendering.
3. A wrong number, unit, acronym, name, date, or negation fails its candidate.
4. One failed segment rerenders without rerendering accepted segments.
5. A revoked or out-of-scope voice consent prevents paid rendering.
6. A provider timeout resumes through Temporal without duplicate accepted output.
7. A final-master critical-token mismatch prevents package success.
8. Replaying a completed workflow returns the same immutable package checksums.
9. A renderer cannot receive a request without current voice rights and tenant
   scope.
10. Rate limiting or fallback creates no duplicate accepted candidate and never
    weakens QA gates.

## Executable behavior specification

Acceptance behavior is authored in Gherkin under `tests/features` and bound with
`pytest-bdd`. Feature files are the readable acceptance contract; step code must
exercise application ports or public interfaces rather than reproduce business
logic. Provider HTTP is replaced at the adapter boundary in normal CI, while
separately marked opt-in contract tests may call provider sandboxes/live APIs.

For every implementation increment the required order is:

1. add or refine one Gherkin scenario and its executable steps;
2. run it and confirm it fails for the missing behavior, not test setup;
3. implement the minimum production behavior to pass;
4. run the focused scenario, then unit/contract tests and the full suite;
5. refactor only while all tests remain green.

No production module may be introduced to make an unexecuted scenario pass.

## Quality eval corpus

The fixture article describes a robotics mapping system and includes two speakers,
technical disagreement, dates, percentages, negative claims, `SLAM`, `LiDAR`,
`C1`, `1.6 Tbit/s`, `21.5 kg`, and named products. Golden evals assert source
support, expected spoken forms, stable speaker assignment, segment repair, package
completeness, and deterministic mastering metadata. Subjective listening uses a
five-point rubric for intelligibility, naturalness, pacing, turn-taking, voice
consistency, artifact absence, and subscription readiness; no category may score
below 4 and subscription readiness must average at least 4.

## Unresolved design risks

- Transcript comparison must tolerate harmless spoken contractions without
  weakening critical-token checks.
- Provider voice/version pinning capabilities differ and may limit bit-identical
  audio reproducibility; provenance reproducibility is mandatory regardless.
- Objective cadence scoring requires calibration against human listening evals.
- Source-anchor validation for LLM adaptation needs adversarial evals, not only
  schema validation.
