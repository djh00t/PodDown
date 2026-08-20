# Source-Bound Production Runtime Design

## Goal

Remove the fixture-only limitation from the durable production workflow while
preserving the reference demo as a visibly separate execution path. A normal
episode command must be able to carry an immutable source/preparation authority
to Temporal, perform preparation in a worker activity, and construct the render
snapshot only from the validated preparation result.

## Boundaries

The reference fixture remains an explicit local/demo configuration. It is not
used as a production content source and is not silently selected when generic
configuration is absent. Deterministic-local and host-local arbitrary-source
execution continue to require an explicitly supplied, source-bound adaptation
configuration; live-provider execution uses the configured OpenAI reasoning
port and never falls back to local reasoning.

This design closes the code-level API-to-worker preparation boundary. It does
not claim hosted Temporal, PostgreSQL, MinIO, live provider, authenticated UAT,
or deployment evidence.

## Architecture

1. The API snapshot contract gains a source-only production form. It contains
   episode identity, source hash, profile identity, execution mode, preparation
   input, and optional publication authority. It does not invent render
   segments or serialize provider secrets.
2. `EpisodeProductionWorkflow` validates the source, executes the preparation
   activity, and obtains a canonical JSON render snapshot from that activity's
   validated result. Existing snapshots that already contain a render snapshot
   remain valid and replay identically.
3. The preparation activity receives an immutable production context and uses a
   worker-owned request factory. The factory resolves profile, treatment,
   lexicon, voice assets, and consent configuration. Live mode injects
   `LiveAdaptationService`; local modes use only explicitly configured
   deterministic/source-bound reasoning.
4. The prepared result carries only JSON-safe render inputs and compact
   preparation references. The workflow verifies episode, version, source,
   execution mode, provider, and critical-token identity before starting the
   child render workflow.
5. Runtime composition registers the generic preparation/stage activities only
   when their explicit content configuration is complete. Missing or malformed
   configuration fails closed before a worker advertises readiness.

## Error and replay behavior

- Missing generic content configuration is a non-retryable configuration
  failure, not a reference-fixture fallback.
- Provider adaptation errors, unsupported claims, missing anchors, consent
  failures, and provider-cost failures remain terminal according to the
  existing policy.
- The source-only production snapshot is the Temporal workflow identity. A
  retry or duplicate command reuses the same workflow ID and preparation
  identity; no second provider call is made for a completed preparation stage.
- Render-input construction rejects changed source bytes, profile/version,
  provider mode, voice consent, or critical-token identity.

## Verification

BDD/TDD coverage will prove:

- a source-only production snapshot is accepted without fabricated segments;
- preparation emits a validated render snapshot for a configured source;
- a configured live adaptation port is used exactly once and local fallback is
  impossible;
- missing profile/voice/treatment configuration fails closed;
- existing reference-fixture snapshots retain their current behavior;
- duplicate/replay inputs preserve the same immutable workflow identity; and
- changed preparation/render evidence is rejected before rendering.

