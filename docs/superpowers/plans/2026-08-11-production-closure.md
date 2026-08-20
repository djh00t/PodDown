# PodDown Production-Closure Implementation Plan

Status: approved execution plan; current `origin/main` baseline is
`7ce7d6f877437dba7dbd0ec1bfbaa9201263c3e3` (PR #40 merged).

This plan closes the audited contract/demo-only boundaries and delivers a repeatable,
honest PodDown production path. Work is executed as bounded 10–15 minute packages,
one Conventional Commit per package, package branches targeting a shared phase branch,
and one normal phase PR targeting `main`. No PR is merged autonomously.

## Operating constraints

- Use isolated git worktrees and keep package write sets disjoint.
- Use BDD followed by TDD for behavior changes; capture RED before implementation.
- Run focused tests, Ruff, strict mypy, `make check`, `make build`, `uv lock --check`,
  `uv pip check`, and `git diff --check` at the applicable gates. `main` CI owns
  `make check-full` and `make quality-gates`; do not run those locally.
- Deterministic-local and host-local demos remain supported but are labelled honestly.
- Live provider calls require explicit opt-in, configured credentials, cost ceilings,
  approved voice rights/consent, and complete provider evidence. External publication
  requires fresh user authorization and an approval record.
- ElevenLabs is the primary live renderer; OpenAI is the primary transcriber and
  Responses-compatible reasoning provider; MinIO is the safe storage demo target.
- Do not add complex UI, Kubernetes, speculative billing, or team administration.
- Use Luna for coordination, Terra for routine packages, and Sol standard only for
  security, contract, integration, and final adversarial review. Avoid Sol High.

## Frozen contracts

### Execution evidence

Add `specs/003-durable-audio-production/contracts/execution-evidence.schema.json` and
matching models. A record contains `schema_version: "1.0"`, execution mode
`deterministic-local|host-local|live-provider`, render evidence
`synthetic-bytes|host-tts|provider-response`, transcript evidence
`script-derived|provider-asr`, publication scope `filesystem|object-storage|external`,
`live_eligible`, and renderer/transcriber provider, model, and request IDs.
Live eligibility requires live-provider mode, provider-response audio, provider-ASR,
valid consent, complete provider metadata and cost evidence, and 100% critical-token
accuracy. Script-derived evidence cannot claim live fidelity.

### Provider route and evidence

`ProviderRoute` contains `route_id`, mode, renderer, transcriber, fallbacks,
`pricing_version`, `max_request_cost`, and `max_episode_cost`.
`ProviderBinding` contains provider, model, optional public voice asset ID,
required capabilities, and a secret reference; secret values never enter records.
`ProviderEvidence` contains operation, provider request ID, model, input/output hashes,
usage, currency, estimated/reconciled Decimal costs, latency, retry count, timestamp,
and evidence kind `synthetic|host-local|provider-live`.

### API, workflow, auth, and storage

Preserve existing API paths and compatibility fields while extending render/publish
commands, command receipt states, and episode status. Add `EpisodeProductionWorkflow`
with `validate_source -> prepare_content -> render_segments -> master ->
final_master_transcription -> package -> optional_publish`; each stage uses immutable
input snapshots, authoritative repository state, transactional outbox events, replay
idempotency, closed failure behavior, and failed-segment-only rerendering.

Add schema-bound `AdaptationEnvelope` with source hash, treatment, model, request ID,
validated adapted turns, anchors, usage, and estimated cost. Add verified OIDC
`AuthenticatedPrincipal` and one-time scoped `PublicationApproval`; tenant/project
headers are local-mode compatibility only.

Add PostgreSQL tenant-scoped schema with UUIDv7 keys, foreign keys, optimistic episode
versions, immutable source/script hashes, voice consent and pronunciation records,
render/candidate/QA/master records, publishing/approval records, usage/cost records,
and transactional outbox. Enable tenant RLS as defense in depth.

Implement content-addressed `S3ObjectStore` with metadata/checksum verification,
idempotent identical puts, mismatch failure, and MinIO integration. Implement durable
S3/RSS/Transistor publication adapters with injected transports, recorded fixtures,
safe errors, receipts, retries, compensation, and explicit live opt-in.

## Workstreams and dependency order

`C contract truth` -> `V live providers/QA` and `D production data plane` -> `A API/Temporal`
-> `M MCP/auth` -> `P publishing` -> `O operations/demo`; `R structured reasoning`
depends on C and D and joins A.

### C — Contract truth

C01 amend approved specs; C02 execution-evidence schema/validator; C03 evidence enums;
C04 provider-route schema/models; C05 provider evidence compatibility; C06 API receipt/status
schemas and compatibility tests; C07 BDD proving script-derived QA cannot claim live fidelity.

### V — Live providers and QA

V01–V03 extract and preserve ElevenLabs client/renderer behavior; V04–V06 OpenAI
transcriber factory, validated settings, registry and route resolution; V07 rights,
allowlist, budget preflight; V08–V10 durable live activity/evidence/cost persistence;
V11–V13 guarded live demo, OpenAI ASR, and opt-in one-segment smoke.

### D — Production data plane

D01 approved Psycopg/Boto3/NATS dependencies; D02 migrations runner; D03–D09 forward-only
PostgreSQL schemas; D10–D13 durable repositories, ledger, and RLS; D14–D15 S3/MinIO;
D16–D17 transactional outbox and NATS JetStream replay; D18 orphan discovery and safe GC.

### A — FastAPI and Temporal

A01–A03 receipt transitions, deterministic dispatcher, status activity; A04–A08
preparation, immutable production workflow, rendering/mastering/ASR/package commits;
A09–A13 API dispatch/status/publish and runtime composition; A14 restart/idempotency BDD;
A15 production CLI path.

### R — Live structured adaptation

R01 envelope schema/parser; R02 request builder; R03 injected Responses transport;
R04 adapt; R05 bounded repair; R06 schema-repair/fail-closed BDD; R07 metering;
R08–R09 model eval/selection; R10 production preparation integration.

### M — Production MCP/auth

M01–M04 auth settings, OIDC validation, scope middleware, durable approval consumption;
M05–M06 HTTP gateway; M07 explicit local/API MCP modes; M08 API integration; M09
authorized resources; M10 skill/eval evidence distinctions.

### P — Durable publishing

P01–P04 durable receipts, service, S3 publication, deterministic RSS; P05–P07
Transistor transport/create/update/delete; P08 retry/compensation/uncertain outcomes;
P09 Temporal publish activity; P10 guarded live contract smoke.

### O — Production readiness and demo closure

O01–O02 Compose wiring and clean-Compose episode smoke; O03–O05 tracing, redacted logs,
SLO metrics; O06–O09 retention/export/deletion/backup/restore; O10–O11 fault tests and
load contract; O12–O14 security/SBOM/signing/release checks; O15–O16 honest reference
demo and live runbook; O17 Signal & Supply UAT; O18 listening evidence; O19 traceability;
O20 clean-checkout demo verifier.

## Phase topology

- `phase/p0-contract-truth`: C01–C07
- `phase/p1-live-content-audio`: V01–V13, R01–R10
- `phase/p2-production-data`: D01–D18
- `phase/p3-api-temporal`: A01–A15
- `phase/p4-agent-publishing`: M01–M10, P01–P10
- `phase/p5-production-demo`: O01–O20

Dependent phase branches are based on the parent phase head. Rebase/retarget and rerun
focused verification after parent merges. Final acceptance requires an API-backed,
restart-safe immutable package, provider-ASR fidelity at 100% critical-token accuracy,
isolated failed-segment recovery, exact checksums, usage/cost/provenance, MinIO-safe
publication, passing tests/quality/security gates, and a clean-checkout repeatable demo.

## Delivery ledger

The coordinating agent maintains package status, owner, tests/BDD, commit, phase PR,
review/CI state, verification evidence, blockers, and demo path in the plan workspace
ledger. Missing or ambiguous requirements are recorded as planning defects before code.
