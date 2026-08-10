# Listenable Local Reference Demo Design

## Problem

The current reference-demo command produces a structurally valid WAV/MP3 but not
listener-ready audio. `DeterministicLocalRenderer` emits a few milliseconds of
hash-derived PCM noise for each segment, so the published episode is roughly
0.1 seconds long and sounds clipped. The fixture also declares a 12-minute
target while containing only a short three-turn script.

## Goal

Make the offline reference-demo command produce an actually listenable,
two-speaker episode without provider credentials or provider spend, while
preserving deterministic test doubles and keeping ElevenLabs behind its
existing explicit live-provider boundary.

## Non-goals

- This change does not dispatch to ElevenLabs or any other hosted provider.
- This change does not infer or invent live voice rights. The reference voices
  remain synthetic, demo-only, and locally consented.
- This change does not make host operating-system TTS bytes reproducible across
  machines. Host engine, version, and selected voices are recorded as local
  provenance.
- This change does not remove the deterministic renderer; it remains available
  for offline unit, BDD, and contract tests.

## Chosen approach

Add a provider-neutral `LocalSpeechRenderer` that invokes an installed local
speech command and normalizes its output through the existing local `ffmpeg`
toolchain:

1. On macOS, use the built-in `say` command.
2. Otherwise, use `espeak-ng` or `espeak` when available.
3. Convert the engine output to mono, 44.1 kHz, 16-bit PCM WAV.
4. Validate non-empty speech-duration audio, sample rate, channel count, peak,
   clipping ratio, and complete WAV framing before returning `RenderedAudio`.
5. Cache one normalized render per `(speaker, voice asset, spoken text,
   sample rate)` so the required three takes preserve candidate evidence without
   invoking the local TTS engine three times for identical content.

The renderer is injected into `run_reference_demo`. Existing direct callers and
tests retain the deterministic renderer unless they explicitly pass the speech
renderer. The `poddown-demo` CLI selects local speech mode by default and keeps
an explicit deterministic mode for contract-only verification. The result mode
and package provenance distinguish `local-system-tts-demo` from
`deterministic-local-demo`.

The reference fixture will be expanded to a source-bound 10–15 minute dialogue
with the existing required technical names, acronyms, dates, percentages,
units, repeated critical tokens, negation, uncertainty, and disagreement. Each
adapted turn will retain source anchors and expected spoken forms; no provider
or finance logic will be added to core modules.

## Data flow

```text
reference Markdown + profile + adaptation + local voice consent
        |
        v
typed content preparation and segmentation
        |
        v
LocalSpeechRenderer (say/espeak) -> ffmpeg normalization -> WAV candidates
        |
        v
durable artifacts + three candidate records + local zero-cost usage
        |
        v
candidate selection -> deterministic mastering -> final-master QA
        |
        v
episode.wav + episode.mp3 + transcripts + provenance + filesystem publication
```

The fail-once/regeneration path remains intact. On a local speech run, the
intentional first failure does not invoke TTS; the regenerated request uses the
same local renderer and its cache. Resume continues to authenticate persisted
candidate, package, and publication evidence before replaying.

## Configuration and provenance

Reference voice fixture rows may declare local voice names for macOS and
espeak-compatible engines. The renderer never treats these names as ElevenLabs
voice IDs. Provenance records:

- `mode=local-system-tts-demo`;
- selected engine and executable path;
- engine version when available;
- speaker-to-local-voice bindings;
- ffmpeg executable/version and normalization command;
- output checksums and audio diagnostics.

If no supported local speech executable or `ffmpeg` is available, the CLI fails
closed with an actionable installation message. It does not silently fall back
to noise, a hosted provider, or an unapproved credential.

ElevenLabs remains a separate live mode. It requires `ELEVENLABS_API_KEY`, an
authorized provider voice mapping, rights/consent evidence, and explicit
operator opt-in. The local reference command never reads or requires that key.

## Acceptance criteria

1. A fresh default `poddown-demo` run produces an MP3 with actual speech audio,
   more than one second of duration, two distinct local speaker bindings, and
   no clipping failure.
2. The reference source has a genuine 10–15 minute target-length dialogue and
   preserves all existing critical-token and disagreement requirements.
3. Three takes per segment, the deliberate failed render, regeneration,
   critical-token accuracy, package artifacts, provenance, usage, and resume
   behavior remain observable.
4. Local mode records zero provider cost and never requires an API key.
5. Missing local tooling fails clearly before package publication.
6. Deterministic unit/BDD tests remain offline and do not invoke `say`,
   `espeak`, ElevenLabs, or other hosted providers.
7. Focused BDD, unit, integration, lint, type, build, documentation, and
   artifact-audio verification pass before branch handoff.

## Verification strategy

- Add BDD scenarios for a listenable local package, explicit deterministic test
  mode, and missing-tool failure.
- Add unit tests with an injected process runner for command construction,
  output normalization, caching, malformed output, and peak/duration rejection.
- Add an integration smoke command on this macOS workstation using the actual
  local engine and inspect the published MP3 with `afinfo`/`ffprobe`.
- Assert the final artifact duration and WAV diagnostics directly; package
  artifact names and metadata alone are insufficient evidence of listenability.
