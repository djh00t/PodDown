# Planning Traceability Audit

## Approved decomposition coverage

| # | Requirement | Owning spec | Planned milestone |
|---:|---|---|---|
| 1 | Markdown/frontmatter | 001 | M0 |
| 2 | Profile configuration | 001 | M0/M1 |
| 3 | Episode job API | 004 | M3 lifecycle, deterministic offline HTTP transport, and restart-safe local SQLite API mode locally verified: [episode lifecycle BDD](../tests/features/episode_lifecycle.feature), [episode API BDD](../tests/features/episode_api.feature), [durable persistence BDD](../tests/features/durable_persistence.feature), [API integration tests](../tests/integration/test_episode_api.py), [durable persistence integration tests](../tests/integration/test_durable_persistence.py), and [verification evidence](verification/durable-persistence.md); PostgreSQL-backed production transport remains pending |
| 4 | Source-bound adaptation | 002 | M1 complete locally; adversarial eval evidence recorded |
| 5 | Dialogue/script model | 002 | M1 complete locally; source-bound canonical script verified |
| 6 | Provider abstraction | 001 | M0 |
| 7 | Voice rights/consent | 001/004 | M0/M3 |
| 8 | Pronunciation engine | 002 | M1 complete locally; deterministic token evidence verified |
| 9 | Segmentation engine | 002 | M1 complete locally; capability-safe segmentation verified |
| 10 | Render orchestration | 003 | M2 foundation plus bounded Temporal orchestration and provider-bound local activity wiring locally verified: [durable BDD](../tests/features/durable_audio.feature), [Temporal BDD](../tests/features/temporal_orchestration.feature), [provider-activity BDD](../tests/features/provider_temporal_render.feature), [durable integration](../tests/integration/test_durable_render.py), [Temporal integration](../tests/integration/test_temporal_orchestration.py), [provider-activity integration](../tests/integration/test_temporal_durable_render_activity.py), [terminal failure integration](../tests/integration/test_temporal_failure_semantics.py), [partial-take retry BDD](../tests/features/provider_temporal_render.feature), [concurrent claim integration](../tests/integration/test_durable_render_concurrency.py), [separate-process claim integration](../tests/integration/test_durable_render_multiprocess.py), and [verification evidence](verification/provider-temporal-render-activity.md) |
| 11 | Candidate-take scoring | 003 | M2 locally verified for deterministic hard-gate selection and stable ranking: [selection tests](../tests/unit/audio/test_selection.py) and [Temporal verification](verification/temporal-orchestration-qa.md); provider-backed scoring remains pending |
| 12 | Transcription/fidelity QA | 001/003 | M0/M2 locally verified for injected normalized transcript QA, audio-bound checksum/empty-text rejection, deterministic local mode, transcription failure mapping, atomic transcription response plus estimated-cost replay, immutable quality replay, and provider usage/cost evidence: [provider BDD](../tests/features/provider_temporal_render.feature), [provider contract tests](../tests/contract/providers/test_openai_transcription.py), [activity tests](../tests/unit/audio/test_activities.py), [quality/unit tests](../tests/unit/audio/test_selection.py), [quality storage tests](../tests/unit/audio/test_storage.py), and [verification evidence](verification/transcription-fidelity-qa.md); external billing reconciliation remains M3 |
| 13 | Critical-token verification | 001/002/003 | M0–M2 locally verified against provider transcript text with segment-only rerender evidence and independent final-master fidelity: [final-master BDD](../tests/features/final_master_qa.feature), [final-master unit tests](../tests/unit/qa/test_final_master.py), and [verification evidence](verification/final-master-qa.md) |
| 14 | Audio quality gates | 003 | M2 locally verified for WAV diagnostics, clipping regression, provider-bound artifact checks, deterministic mastering input/output gates, and independent final-master media preflight: [diagnostic tests](../tests/unit/audio/test_diagnostics.py), [mastering verification](verification/deterministic-mastering.md), and [final-master verification](verification/final-master-qa.md); listening gates remain pending |
| 15 | Mastering | 003 | M2 deterministic mastering and final-master QA boundaries locally verified for stable ordering, profile validation, injected ffmpeg/ffprobe boundaries, exact-byte transcription binding, WAV/MP3 inspection, and immutable provenance: [mastering BDD](../tests/features/mastering.feature), [final-master BDD](../tests/features/final_master_qa.feature), and [verification evidence](verification/final-master-qa.md) |
| 18 | CLI | 005 | M4 provider-free preview/render/publish/status command client, stable exit/JSON contracts, config precedence, resumable polling, atomic package installation, and local verification evidence: [CLI BDD](../tests/features/cli.feature), [preview BDD](../tests/features/preview.feature), [preview unit tests](../tests/unit/test_preview.py), [preview CLI tests](../tests/integration/test_preview_cli.py), and [verification evidence](verification/cli-github-automation.md); live provider rendering and external publication remain pending |
| 16 | Package/provenance | 001/003/004 | M0–M5 locally verified for content-addressed immutable artifacts, deterministic nine-file generation, final checksum and critical-token binding, replay, conflict preservation, tenant/project-scoped filesystem object storage, and compensated publication references: [package BDD](../tests/features/package.feature), [package-generation BDD](../tests/features/package_generation.feature), [object-storage BDD](../tests/features/tenant_object_storage.feature), [artifact unit tests](../tests/unit/test_artifacts.py), [package unit tests](../tests/unit/test_packages.py), [object-storage integration tests](../tests/integration/test_tenant_object_storage.py), [publishing integration tests](../tests/integration/test_publishing.py), [immutable package verification](verification/immutable-package.md), [package-generation verification](verification/package-generation.md), [tenant object-storage verification](verification/tenant-object-storage.md), and [publishing verification](verification/publishing.md); durable orphan GC and hosted storage remain pending |
| 17 | Publishing adapters | 007 | M5 locally verified for QA/checksum-gated immutable publication, scoped filesystem/ObjectStore adapters, deterministic multi-episode RSS, recorded Transistor contracts, explicit authorization/disclosure/provenance, retry/compensation, and mutation receipts: [publishing BDD](../tests/features/publishing.feature), [publishing unit/integration tests](../tests/unit/test_publishing_service.py), [publishing adapter tests](../tests/unit/test_publishing_adapters.py), [Transistor contract tests](../tests/contract/providers/test_transistor_publishing.py), and [verification evidence](verification/publishing.md); live S3/Transistor transport and durable cross-process receipts remain pending |
| 19 | MCP server | 006 | M5 locally verified for tenant-authenticated preview/render/publish/status/episode tool schemas, MCP initialize negotiation, tool-array discovery, CallToolResult envelopes, closed output properties, nested result redaction, fail-closed approval/error handling, concurrent one-time approval consumption, and resilient stdio JSON-lines plumbing: [agent integration BDD](../tests/features/agent_integrations.feature), [MCP contract tests](../tests/contract/test_agent_mcp_contract.py), [MCP unit/integration tests](../tests/unit/test_agent_mcp.py), and [verification evidence](verification/agent-integrations.md); real API gateway and production MCP transport remain pending |
| 20 | PodDown skill | 006 | M5 locally verified for versioned source-bound preparation guidance, preview-before-render choice, side-effect/refusal boundaries, and deterministic eval fixtures packaged in the wheel: [agent integration BDD](../tests/features/agent_integrations.feature), [skill evals](../skills/poddown/v1/evals.json), and [verification evidence](verification/agent-integrations.md); publication/registration remains pending |
| 21 | GitHub Action | 005 | M4 local pull-request preview plus separately protected merge/tag/manual render and publication jobs with concurrency keys, environment approvals, secret isolation, and job summaries verified: [CLI BDD](../tests/features/cli.feature) and [verification evidence](verification/cli-github-automation.md); external environment credentials remain deployment-owned |
| 22 | Multitenancy | 004 | M3 tenant-scoped lifecycle repository, SQLite persistence, object keys, and HTTP boundary locally verified: [episode lifecycle BDD](../tests/features/episode_lifecycle.feature), [episode API BDD](../tests/features/episode_api.feature), [durable persistence BDD](../tests/features/durable_persistence.feature), [object-storage BDD](../tests/features/tenant_object_storage.feature), and [verification evidence](verification/durable-persistence.md); PostgreSQL row-level isolation and auth middleware remain pending |
| 23 | Usage/cost metering | 004 | M3 append-only tenant-scoped SQLite usage/cost ledger locally verified with exact Decimal serialization, idempotent provider-request replay, and conflict protection: [persistence unit tests](../tests/unit/test_persistence.py), [persistence integration tests](../tests/integration/test_durable_persistence.py), and [verification evidence](verification/durable-persistence.md); provider-invoice reconciliation remains pending |
| 24 | Signal & Supply integration | 008 | M6 |

## Additional requirements found by whole-product audit

The original decomposition did not separately own runtime deployment,
observability, backup/restore, retention/deletion, supply-chain security, load
testing or release operations. Spec 009 owns these launch requirements. They do
not expand the MVP feature set; they make the approved service operable.

## Readiness summary

- Product boundary: fully assigned.
- Functional specifications: complete at subsystem level.
- Dependency/order plan: complete through launch.
- M1 content intelligence: complete locally with deterministic adversarial eval
  evidence; it is not audio-rendering or production-readiness evidence.
- M2 durable audio foundation: locally verified only for rights/capability
  preflight, deterministic local takes, immutable artifacts, usage/cost records,
  and replay across fresh service instances; later M2 work remains pending.
- M2 Temporal orchestration and audio QA boundary: locally verified for bounded
  retry, three-take selection, hard-gate precedence, failed-segment repair,
  structured failure, completed-workflow replay, provider-bound activity
  wiring, rights-before-dispatch, immutable artifact persistence, and one
  local cost event per candidate across post-persist replay. Non-retryable
  rights failures now terminate on the original attempt, successful takes
  remain selectable when a sibling take exhausts transient retries, unknown
  terminal activity failures report configuration/activity gates, and the
  filesystem claim prevents concurrent local-process dispatch for one
  idempotency key; the crash window after provider dispatch and before record
  save still belongs to hosted provider idempotency and reconciliation.
  injected transcription/fidelity QA now records normalized provider evidence
  plus an atomic estimated-cost event, binds transcript checksums to audio,
  rejects empty evidence, maps terminal and retryable transcription failures to
  the transcription gate, and replays persisted quality without a second
  renderer or transcription dispatch. External billing reconciliation remains
  M3-owned. The deterministic-local mode is explicitly labeled and zero-cost.
  Deterministic mastering and final-master QA now locally verify stable segment
  assembly, profile-bound WAV/MP3 media inspection, mandatory MP3 metadata,
  injected ffprobe failure handling, read-only provenance/checksum evidence,
  exact-byte final-master transcription binding, critical-token scoring, and
  retry/terminal provider mapping. Immutable local artifact storage and
  nine-file package manifest assembly and pure generation from verified
  final-master evidence now pass schema-compatible checks with deterministic
  replay and conflict preservation; object-storage integration and publication
  remain pending.
- Task-level implementation plan: intentionally produced just-in-time per
  milestone so measured interfaces and audio quality inform the next plan.
- Publishing, Signal & Supply, and production readiness remain pending in
  their assigned future milestones. The API, CLI, and agent integrations now
  have deterministic local HTTP/SQLite/stdio paths; live provider rendering,
  external publication, and production MCP transport remain explicit
  deployment-boundary work.
