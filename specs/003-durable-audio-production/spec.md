# Specification: Durable Audio Production

**Status:** Planned

## Goal

Render, select, repair, master and verify a canonical script as unattended,
subscription-quality audio through a resumable and idempotent Temporal workflow.

## Workflow contract

`EpisodeRenderWorkflow` snapshots immutable input IDs, fans out segment activities,
selects candidates, repairs only failed segments, masters accepted audio, runs final
QA and commits an episode package. Activity idempotency keys derive from episode
version, stage, segment, attempt and candidate. Workflow retries never overwrite an
accepted candidate or immutable artifact.

Each difficult or previously failed segment receives up to three independent takes
per attempt. Selection first enforces rights, transcript, critical-token,
pronunciation and audio hard gates; only then does a weighted soft score rank
passing candidates. Attempt exhaustion produces structured terminal evidence.

## Audio diagnostics

Decode audio before QA. Validate container, codec, duration, sample rate, channel
count and expected frame length. Measure clipping/true peak, integrated loudness,
silence anomalies, speech rate, repetition, discontinuity, noise and rendering
artifacts. Thresholds are versioned in the resolved profile.

## Mastering

ffmpeg performs deterministic decode, resample, channel normalization, bounded
silence adjustment, crossfades, EQ, compression, limiting, loudness normalization,
intro/outro insertion and WAV/MP3 encoding. The mastering command, ffmpeg version,
filters and input/output checksums are provenance. Mastering never synthesizes or
rewrites speech.

The final master is transcribed independently and must pass full critical-token,
duration and audio gates. Failure blocks packaging and publication.

## Acceptance behavior

1. A single failed segment rerenders without dispatching accepted segments again.
2. Temporal restart resumes without duplicate accepted candidates or cost events.
3. Rate limits, timeouts and worker loss follow bounded retry policy.
4. Candidate selection cannot trade a hard-gate failure for a better soft score.
5. Invalid/truncated audio fails before transcription or mastering.
6. The mastered WAV/MP3 meet profile loudness, peak, format and duration limits.
7. Final-master token mismatch blocks success.
8. Replaying a completed workflow returns the existing immutable package.

## Quality target

Objective gates must pass. A blinded listening panel scores intelligibility,
naturalness, pacing, turn-taking, consistency, artifact absence and subscription
readiness; every dimension is at least 4/5 and mean subscription readiness is at
least 4/5 before the vertical slice is accepted.

