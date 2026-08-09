# Planning Traceability Audit

## Approved decomposition coverage

| # | Requirement | Owning spec | Planned milestone |
|---:|---|---|---|
| 1 | Markdown/frontmatter | 001 | M0 |
| 2 | Profile configuration | 001 | M0/M1 |
| 3 | Episode job API | 004 | M3 |
| 4 | Source-bound adaptation | 002 | M1 complete locally; adversarial eval evidence recorded |
| 5 | Dialogue/script model | 002 | M1 complete locally; source-bound canonical script verified |
| 6 | Provider abstraction | 001 | M0 |
| 7 | Voice rights/consent | 001/004 | M0/M3 |
| 8 | Pronunciation engine | 002 | M1 complete locally; deterministic token evidence verified |
| 9 | Segmentation engine | 002 | M1 complete locally; capability-safe segmentation verified |
| 10 | Render orchestration | 003 | M2 foundation plus bounded Temporal orchestration and provider-bound local activity wiring locally verified: [durable BDD](../tests/features/durable_audio.feature), [Temporal BDD](../tests/features/temporal_orchestration.feature), [provider-activity BDD](../tests/features/provider_temporal_render.feature), [durable integration](../tests/integration/test_durable_render.py), [Temporal integration](../tests/integration/test_temporal_orchestration.py), [provider-activity integration](../tests/integration/test_temporal_durable_render_activity.py), [terminal failure integration](../tests/integration/test_temporal_failure_semantics.py), [partial-take retry BDD](../tests/features/provider_temporal_render.feature), [concurrent claim integration](../tests/integration/test_durable_render_concurrency.py), [separate-process claim integration](../tests/integration/test_durable_render_multiprocess.py), and [verification evidence](verification/provider-temporal-render-activity.md) |
| 11 | Candidate-take scoring | 003 | M2 locally verified for deterministic hard-gate selection and stable ranking: [selection tests](../tests/unit/audio/test_selection.py) and [Temporal verification](verification/temporal-orchestration-qa.md); provider-backed scoring remains pending |
| 12 | Transcription/fidelity QA | 001/003 | M0/M2 locally verified for injected normalized transcript QA, audio-bound checksum/empty-text rejection, deterministic local mode, transcription failure mapping, atomic transcription response plus estimated-cost replay, immutable quality replay, and provider usage/cost evidence: [provider BDD](../tests/features/provider_temporal_render.feature), [provider contract tests](../tests/contract/providers/test_openai_transcription.py), [activity tests](../tests/unit/audio/test_activities.py), [quality/unit tests](../tests/unit/audio/test_selection.py), [quality storage tests](../tests/unit/audio/test_storage.py), and [verification evidence](verification/transcription-fidelity-qa.md); external billing reconciliation remains M3 |
| 13 | Critical-token verification | 001/002/003 | M0–M2 locally verified against provider transcript text with segment-only rerender evidence; final-master verification remains pending |
| 14 | Audio quality gates | 003 | M2 locally verified for WAV diagnostics, clipping regression, provider-bound artifact checks, and hard gates: [diagnostic tests](../tests/unit/audio/test_diagnostics.py), [local renderer regression](../tests/unit/audio/test_local.py), [provider-activity verification](verification/provider-temporal-render-activity.md), and [transcription/fidelity verification](verification/transcription-fidelity-qa.md); final-master and listening gates remain pending |
| 15 | Mastering | 003 | M2 |
| 16 | Package/provenance | 001/003 | M0/M2 |
| 17 | Publishing adapters | 007 | M5 |
| 18 | CLI | 005 | M4 |
| 19 | MCP server | 006 | M5 |
| 20 | PodDown skill | 006 | M5 |
| 21 | GitHub Action | 005 | M4 |
| 22 | Multitenancy | 004 | M3 |
| 23 | Usage/cost metering | 004 | M3 |
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
  Mastering, final-master QA, packaging, and publication remain pending.
- Task-level implementation plan: intentionally produced just-in-time per
  milestone so measured interfaces and audio quality inform the next plan.
- Audio rendering, Temporal, API, CLI, publishing, MCP, Signal & Supply, and
  production readiness remain pending in their assigned future milestones.
