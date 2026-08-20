# PodDown Product Delivery Plan

**Baseline:** Specs 001–009 in `specs/`
**Planning horizon:** Working product through production-ready customer-one release

## Definition of working product

PodDown is working when the difficult non-finance fixture travels through the real
system—Markdown intake, source-bound script, real two-voice rendering, automatic
repair, deterministic mastering, final QA and immutable package—through both CLI
and asynchronous API, and consistently clears the subscription-readiness rubric.
Publishing, MCP and Signal & Supply then extend that proven pipeline.

## Delivery sequence

| Milestone | Deliverable | Exit evidence |
|---|---|---|
| M0 Foundation | Merged PR #1 contracts and adapters | 68 tests, provider contracts, branch coverage gate |
| M1 Content intelligence | Treatment, canonical script, lexicons, token extraction, segmentation | Complete locally: source-bound adversarial evals and `002` BDD pass; no provider or live-mode claim |
| M2 Audio engine | Temporal render/takes/QA/repair/master/package | [Bounded local foundation evidence](verification/durable-audio-foundation.md), [transcription/fidelity evidence](verification/transcription-fidelity-qa.md), [deterministic mastering evidence](verification/deterministic-mastering.md), and [host-local reference verification](verification/clean-checkout.md); provider-ASR fidelity, listening gates, and hosted recovery remain pending |
| M3 Episode service | PostgreSQL/S3 model, FastAPI async jobs, metering, Compose | Local API-to-Temporal, PostgreSQL/RLS, ledger, outbox, S3/MinIO, restart/idempotency, and tenant-boundary tests; hosted service and authenticated UAT remain pending |
| M4 Developer experience | CLI and GitHub Action | Provider-free preview/render/status/publish/download contracts and local package verification; hosted workflow credentials remain deployment-owned |
| M5 Distribution | Publishing adapters, MCP and PodDown skill | Local filesystem/S3/RSS/Transistor ports, durable receipts, HTTP/MCP/resource-link boundaries, and agent eval suites; live mutation and deployed MCP remain pending |
| M6 Customer one | Signal & Supply profile/lexicons/workflow | Finance token fidelity plus non-finance regression pass |
| M7 Launch gate | Security, observability, recovery, capacity and releases | Local contracts/scans/SBOM are recorded; production still requires hosted restore/outage/load/telemetry, signing, staged deployment, and authenticated UAT |

## Implementation plans

Each milestone receives a task-level Superpowers plan immediately before execution,
after the preceding contract is green. This prevents later plans from hard-coding
interfaces invalidated by measured audio behavior. The required plan set is:

1. `content-intelligence.md`: domain models; reasoning port; anchored adaptation;
   repair; lexicons; extraction; segmentation; adversarial eval corpus.
2. `durable-audio-production.md`: Temporal workflow; artifact store; take fan-out;
   transcription; hard/soft scoring; repair; ffmpeg mastering; final QA; package.
3. `episode-platform.md`: SQL model/migrations; repositories; object storage;
   outbox/events; FastAPI; auth/tenancy; idempotency; metering; Compose.
4. `cli-github-automation.md`: CLI configuration/output; package download;
   GitHub Action validation/render/publish protection.
5. `publishing.md`: filesystem/S3/RSS/Transistor ports, receipts, disclosure and
   authorization.
6. `agent-integrations.md`: MCP schemas/server, skill package/evals, optional
   preview UI decision.
7. `signal-and-supply.md`: customer profile, rights, lexicons, disclosure,
   repository workflow and acceptance episode.
8. `production-readiness.md`: telemetry, security, retention, recovery, load,
   supply chain and release automation.

Every plan must use exact files/interfaces, checkbox steps, BDD-first RED/GREEN,
focused verification, full-suite verification and a bounded Conventional Commit.

## Milestone task map

### M1 — Content intelligence

1. Define Gherkin for anchored adaptation, unsupported claims, dialogue quality,
   lexicon precedence, token extraction and segmentation.
2. Add immutable treatment/script/anchor/lexicon/token/segment models.
3. Implement source block indexing and anchor validation.
4. Add structured reasoning-provider port and deterministic fake.
5. Implement adaptation and bounded turn-level repair.
6. Implement layered immutable pronunciation resolution.
7. Implement critical-token extraction with occurrence identity.
8. Implement deterministic capability-aware segmentation.
9. Build adversarial and human-scored eval corpus; record verification.

### M2 — Durable audio production

Status: locally reconciled. Audio rendering, Temporal orchestration, provider
boundaries, deterministic/host-local mastering, final-master QA, and immutable
package evidence have bounded local evidence; live provider-ASR fidelity and
hosted recovery remain pending.

1. Define BDD for retries, fan-out, takes, partial repair, mastering and replay.
2. Add Temporal workflow/activity contracts and local test environment.
3. Add content-addressed artifact storage port and filesystem test adapter.
4. Orchestrate rights-checked candidate rendering and cost recording.
5. Implement transcription alignment and pronunciation verification.
6. Implement objective audio diagnostics and versioned hard/soft scoring.
7. Implement candidate ranking and segment-only bounded repair.
8. Implement deterministic ffmpeg stitching/mastering and media inspection.
9. Generate transcript, VTT, chapters, notes, QA, provenance and manifest.
10. Run final-master transcription/QA and immutable package commit.
11. Execute live ElevenLabs/OpenAI robotics episode within a fixed cost cap.
12. Calibrate gates through blinded listening; iterate until quality target passes.

### M3 — Episode service

Status: locally reconciled. API, persistence, tenancy, metering, object storage,
outbox, and Compose contracts are covered by local ports and tests; real service
deployment, migrations, isolation, and recovery remain pending.

1. Define BDD for episode lifecycle, isolation, idempotency, status and metering.
2. Implement SQLAlchemy 2 models and Alembic migrations for all required entities.
3. Add tenant-scoped repositories and database isolation tests.
4. Add S3-compatible immutable artifact repository and checksum verification.
5. Add transactional outbox and NATS JetStream publisher.
6. Implement FastAPI schemas, auth context, problem details and five endpoints.
7. Connect API commands to Temporal with stable workflow/job IDs.
8. Record usage/cost events and reconciliation reports.
9. Add Dockerfile/Compose and restart/idempotency end-to-end tests.

### M4 — Developer experience

Status: locally reconciled for the provider-free path; hosted credentials and
external execution remain deployment-owned.

1. Implement CLI BDD, config precedence and stable JSON/error contract.
2. Add preview/render/status/publish commands as API clients.
3. Add verified package download and `--wait` polling.
4. Add composite/container GitHub Action with fork-safe validation.
5. Add concurrency/idempotency and protected publish environment examples.
6. Run a repository fixture from commit through package artifact.

### M5 — Distribution

Status: locally reconciled for adapters, MCP/resource links, approvals, and the
PodDown skill; deployed/authenticated transport and live publication remain
pending.

1. Implement publisher port and protected publication state machine.
2. Deliver filesystem and S3 adapters first; verify exact checksums.
3. Deliver deterministic generic RSS and Transistor contract adapter.
4. Add disclosure resolution and publication receipts to provenance.
5. Implement MCP transport over public application services.
6. Create PodDown skill with tool-choice, fidelity and approval evals.
7. Decide widget only from observed approval/preview usability evidence.

### M6 — Signal & Supply

Status: implemented locally in M6 with deterministic offline evidence; live
provider, external publication, deployment, and authenticated UAT remain
pending.

1. Define customer profile and synthetic-presenter disclosure.
2. Register voice assets/consents without embedding provider IDs in source.
3. Curate versioned finance, markets, semiconductor and supply-chain lexicons.
4. Add finance critical-token eval articles and counter-thesis rubric.
5. Integrate merge/release workflow and protected publication target.
6. Produce and review the first acceptance episode.
7. Confirm non-finance fixture and core dependency audit remain clean.

### M7 — Production readiness

Status: local contracts and scans reconciled. Production readiness still requires
live providers, hosted recovery, authenticated UAT, measured capacity,
observability, signing, and staged deployment evidence.

1. Add OpenTelemetry traces, structured logs, metrics, dashboards and alerts.
2. Threat-model tenant, voice, provider, artifact, MCP and publishing boundaries.
3. Add authorization, redaction, dependency, image and secret scanning gates.
4. Define/test retention, export and deletion workflows.
5. Automate backup and perform PostgreSQL/object-store restore drill.
6. Run provider-failure, worker-loss, budget and partial-publication fault tests.
7. Load test API and workers; document measured scaling thresholds.
8. Configure semantic-release, SBOM, signing and staged deployment.
9. Complete trademark/domain/PyPI name clearance before public launch.

## Cross-cutting quality gates

- pytest-bdd scenario exists and is observed RED before each behavior.
- Core line and branch coverage remain at least 80%; changed modules target 95%.
- Ruff, strict mypy, schemas, package build, docs and dependency lock pass.
- Provider tests are transport-injected by default; live tests require explicit
  marker, environment opt-in and cost cap.
- All rights, tenant, critical-token and publish-authorization checks are hard
  gates and have negative tests.
- Each milestone has an independent code review with no unresolved Critical or
  Important findings.
- Verification records include commands, results, fixture/version IDs, costs and
  listening scores; claims of completion cite fresh evidence.

## Decisions intentionally deferred

- Exact LLM adaptation model: select through eval quality/cost after M1 fixtures.
- Exact objective cadence weights: calibrate against M2 listening results.
- UI: no product UI until CLI/API pipeline clears quality target.
- Kubernetes/service split: only after measured Compose scaling/isolation need.
- Additional voice/publishing providers: only after the two initial providers and
  first publishing targets prove the port contracts.
- Billing/team administration: outside this delivery plan; metering data is ready
  for later billing without putting billing in the correctness path.

These are controlled experiments or explicit product exclusions, not missing
requirements.
