# Specification: Episode Platform

**Status:** Planned

## Goal

Provide the authoritative multi-tenant episode model and asynchronous API around
the audio pipeline without building billing or team administration.

## Model

PostgreSQL stores tenant-scoped `Tenant`, `Project`, `Episode`, `EpisodeVersion`,
`SourceDocument`, `Script`, `ScriptVersion`, `Speaker`, `VoiceProfile`,
`VoiceAsset`, `VoiceConsent`, `PronunciationLexicon`, `PronunciationEntry`,
`RenderJob`, `RenderSegment`, `RenderCandidate`, `QAResult`, `Transcript`,
`AudioMaster`, `PublishingTarget`, `Publication`, `UsageEvent` and `ProviderCost`.
UUIDv7 identifiers, UTC timestamps, optimistic versions and explicit state
transitions are used. Tenant ID is mandatory in repositories, unique constraints,
events and object-storage keys.

Large immutable files live in S3-compatible storage with content-addressed keys,
SHA-256 and media metadata. PostgreSQL stores references and authoritative state.
NATS JetStream publishes transactional-outbox integration and usage events; it is
not workflow state or the system of record.

## API

- `POST /v1/episodes` validates and snapshots source/profile, returning `202`.
- `GET /v1/episodes/{id}` returns the current immutable version summary.
- `GET /v1/episodes/{id}/status` returns stage, progress and structured failure.
- `POST /v1/episodes/{id}/render` creates/reuses an idempotent render job.
- `POST /v1/episodes/{id}/publish` requires explicit publish authorization.

Mutations accept idempotency keys. Responses use versioned schemas and stable
problem details. HTTP requests never wait for the full workflow. Artifact download
uses short-lived authorized URLs.

## Metering

Every provider request and compute/storage operation emits an immutable usage event
with tenant, project, job, provider request, units, currency, estimated cost and
reconciled cost. Correctness gates never depend on billing state in the MVP.

## Acceptance behavior

1. Cross-tenant reads, writes, references and object keys are impossible.
2. Duplicate requests with one idempotency key produce one resource/job.
3. Invalid voice consent fails before a render activity or cost event.
4. API restart does not lose or duplicate durable workflow state.
5. Status exposes actionable stage/failure without credentials or source leakage.
6. Usage and provider costs reconcile to every dispatched provider request.
7. State transitions reject regression and illegal publish-before-QA paths.

