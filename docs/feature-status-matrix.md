# PodDown feature status matrix

This matrix separates implementation evidence from production evidence. The
authoritative merged baseline is `main` at `7ce7d6f`; the rows described as
local reconciliation are uncommitted working-tree evidence and are not yet
published.

| Capability | Evidence now available | Status | Remaining proof |
|---|---|---|---|
| Source-bound content and critical tokens | Source bytes, anchors, claims, pronunciation, adaptation, segmentation, and critical-token regression coverage | Delivered locally | Provider-ASR fidelity run |
| Deterministic-local execution | Clean-main compile/demo/resume verifier; 100% script-derived critical-token accuracy; manifest `359928a7…`; 306 replayed takes | Delivered | Keep distinct from speech/live evidence |
| Host-local reference speech | Fresh reconciled run independently verified by FFprobe/provenance: 650.031-second mono 44.1 kHz MP3, manifest `d6c1f8cd…`, 307 render requests, one regenerated segment, 306 replayed takes, zero cost; fresh Compose workflow also completed a 646.759-second mono 44.1 kHz package with canonical manifest `cd7fb13a…8de15` and zero provider cost | Local reconciliation and cross-process integration verified | Obtain release UAT |
| ElevenLabs rendering | Guarded route, rights, consent, cost, renderer, durable-evidence contracts, and an explicit one-segment render+ASR smoke harness; [live runbook](verification/live-provider-runbook.md) | Local runtime wiring; smoke is opt-in and currently unrun | Approved credentialed provider run and reconciled cost |
| OpenAI transcription/reasoning | Injected transports, settings, R09 source-fidelity selection consumed by live preparation, adaptation evidence, and worker ASR boundary; [live runbook](verification/live-provider-runbook.md) | Local runtime wiring | Approved credentialed provider-ASR/reasoning run and authenticated UAT |
| Critical-token hard gate | Deterministic and injected-transcript QA | Local gate verified | 100% against provider ASR |
| Temporal workflow | Local API-to-Temporal, source-only preparation, stage, outbox, object-maintenance, failure-boundary integrations, and a disposable Compose API/worker restart during `rendering` followed by `packaged` status with manifest `32a9ebeb…` | Local cross-process restart/recovery verified | Hosted restart, recovery, and capacity evidence |
| PostgreSQL/RLS/ledger/receipts | Repository-compatible forward migrations and tenant repositories share `app.tenant_id`; D13 policy contract and DSN-gated RLS integration passed 3 tests against local PostgreSQL 16.4 with a non-superuser, non-BYPASSRLS role, plus a D18 runtime-catalog object-reference/inventory/orphan-cleanup test and concurrent API/worker bootstrap regression | Local contract and opt-in PostgreSQL integration verified | Run pooled isolation, hosted restart, and restore evidence |
| NATS transactional outbox | Tenant-scoped outbox, stable message identity, bounded relay, retry evidence, local wire integration, and 2 passing tests against the pinned local NATS image | Local contract and opt-in NATS integration verified | Real NATS/PostgreSQL outage and cross-process replay evidence |
| S3/MinIO object storage | Boto/injected transport, content-addressed references, inventory/orphan policy, opt-in maintenance activity, and 3 passing tests against the pinned local MinIO image | Local contract and opt-in MinIO integration verified | MinIO-backed cross-process checksum/idempotency and recovery UAT |
| FastAPI command/status/publish paths | Provider-free API, source-only snapshot boundary, PostgreSQL/Temporal composition tests, and fresh status projection of the canonical Temporal package manifest | Local cross-process integration verified | Hosted dispatch and authenticated UAT |
| OIDC/JWT tenant scope | Local verifier-to-FastAPI integration and compatibility-mode boundary | Local contract only | Deployed issuer/audience/key rotation and authenticated UAT |
| MCP/resource links | Local schemas, redaction, scoped approvals, signed links, resource routes, and injected gateway tests | Local gateway integration verified | MinIO-backed retrieval and deployed authenticated UAT |
| Filesystem/RSS/Transistor publication | Local adapters, immutable receipts, PostgreSQL receipt replay, and fail-closed uncertain outcomes | Local runtime/contract wiring | Cross-process durable drill; authorized external publication |
| Retention/export/backup | Fail-closed policy, exact local archive round-trip, checksums, tamper/path-escape rejection | Local adapter verified | Hosted PostgreSQL/object-store backup and restore |
| Health/telemetry/SLOs | Redacted event/metric/readiness contracts and Compose assertions | Local contract only | Live probes, tracing backend, dashboards, and alerts |
| Fault/budget/capacity | Bounded retry/failure behavior and admission-limit contracts | Partial | Live outage/budget drills and measured load thresholds |
| Security/release | Secret-boundary tests, lockfile, SBOM, local Trivy/Gitleaks scans, and fail-closed release evidence | Partial | Credential rotation, signing, staged deployment, signed artifact |
| Signal & Supply | Source-bound offline fixture and deterministic rendering | Delivered locally | Live providers, deployment, publication, authenticated UAT |

Overall production readiness is **not established**. The remaining hard gates
are live provider-ASR fidelity, hosted durable-service recovery, MinIO-backed
publication, authenticated UAT, recovery evidence, and deployment/security
release evidence.

The latest full non-live run for the reconciled overlay passed `1999 passed, 11
skipped, 2 deselected, 14 compatibility warnings` with `80.05%` branch
coverage. This is local evidence;
it does not represent CI, merge, deployment, live-provider, or production
readiness evidence.
