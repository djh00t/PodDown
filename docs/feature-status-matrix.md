# PodDown feature status matrix

This matrix separates implementation evidence from production evidence. The
merged `main` baseline is `7ce7d6f`; rows marked **local reconciliation** are
present only in the current preserved-worktree reconciliation and have not been
committed or published from this checkout.

| Capability | Current evidence | Status | Remaining proof or work |
|---|---|---|---|
| Source bytes, anchors, canonical claims, pronunciation, critical tokens | M1 tests, source-bound fixtures, source-only worker preparation, durable preparation round-trip, and full-suite regression coverage | Delivered locally | Provider-ASR fidelity still needs a live run |
| Deterministic-local execution | Merged reference contracts/tests, direct clean-main fixture/compile checks, and a fresh clean-main verifier demo/resume with 100% critical-token accuracy, immutable manifest `359928a7…`, and 306 replayed takes | Delivered | Must remain explicitly non-speech/non-live |
| Host-local reference speech demo | The preserved reconciliation correction completed a fresh local-speech run; independent FFprobe/provenance verification accepted a 650.031-second mono 44.1 kHz MP3, 100% script-derived critical-token accuracy, zero cost, manifest `d6c1f8cd…`, 307 render requests, one deliberately regenerated segment, and 306 replayed takes on resume | Local reconciliation verified | Deliver the duration-scaled FFmpeg correction and verifier into `main`, then obtain release UAT |
| Live ElevenLabs rendering | Guarded adapter, route, rights, consent, cost contracts, and worker-scoped durable evidence recorder | Local runtime wiring | Credentialed opt-in call, provider response evidence, and cost reconciliation |
| OpenAI transcription and reasoning | Injected transports, settings, source-only live preparation, adaptation evidence recorder, and worker ASR evidence path | Local runtime wiring | Credentialed provider-ASR/reasoning run and authenticated UAT |
| Critical-token hard gate | Deterministic and injected-transcript QA | Local gate verified | 100% against real provider ASR |
| Temporal orchestration | Local workflow/activity contracts and full local Temporal test coverage; source-only command dispatch now crosses validate/prepare/render/master/QA/package boundaries with compact replay payloads; real local Temporal integrations cover API dispatch, outbox relay, object maintenance, fail-closed stage handling, and the complete production child/stage boundary | Local integration verified | Hosted worker/API restart, failure recovery, and capacity evidence |
| PostgreSQL, RLS, ledger, receipts, migrations | Historical P2 repositories/schema/tests reconciled; Compose API/worker select PostgreSQL; live-provider evidence and configured publication receipts use transaction-bound PostgreSQL stores with no production SQLite fallback | Local runtime wiring | Real PostgreSQL migration, isolation, restart, and restore drill |
| Transactional outbox / NATS JetStream | Tenant-scoped PostgreSQL outbox, stable JetStream message identity, bounded async relay activity, one-shot tenant-scoped Temporal relay workflow with three-attempt retry, real local Temporal wire-serialization integration, retry-attempt recording, and explicit Compose opt-in | Local runtime wiring | Real NATS/PostgreSQL relay, replay, outage, and operator scheduling evidence |
| S3-compatible object storage / MinIO | Boto/injected transport, durable references, worker publication composition, paginated inventory listing, durable first-seen observations, explicit S3/reference-aware grace-period cleanup composition, an opt-in Temporal `maintain_objects` activity, and a real local Temporal wire test for the worker-clock cleanup report | Local runtime wiring | Running MinIO checksum/idempotency, scheduled GC, and recovery UAT |
| FastAPI preview/command/status/publish paths | Local provider-free `/v1/preview` BDD/integration tests, HTTP/SQLite tests, PostgreSQL/Temporal runtime composition, and source-only configured-content snapshot boundary | Local integration verified | Hosted runtime dispatch and authenticated UAT |
| OIDC/JWT authentication and tenant scope | Local validators/middleware, real JWT-to-FastAPI integration, and compatibility mode | Local contract only | Deployed issuer/audience configuration and authenticated UAT |
| MCP and resource links | Local schemas, redaction, tenant/project/episode/target-scoped approvals, stdio tests, provider-free FastAPI preview wiring, authenticated signed-resource route/manifest links, PostgreSQL/S3 runtime reader composition, and a transport-injected HTTPS/local `HttpAgentGateway` with 60 affected-suite tests | Local gateway/API integration verified | Run MinIO-backed retrieval, wire remaining tools into the deployed authenticated runtime, and complete production UAT |
| Filesystem/RSS publication | Local adapters, immutable receipts, and configured-worker PostgreSQL receipt replay | Local runtime wiring | Real PostgreSQL cross-process recovery drill |
| Transistor publication | Guarded injected HTTPS create/update boundary; uncertain outcomes fail closed | Local contract only | Explicit authorized external publication; delete endpoint/semantics remain provider-bound |
| Retention and deletion decisions | O06–O09 fail-closed policy and authorization contracts | Local contract only | Authorized tenant mutation, audit persistence, legal policy |
| Tenant export and backup/restore manifests | Exact scoped byte/checksum verification plus an idempotent local filesystem archive round-trip that rejects tampering, path escapes, symlinks, and unexpected entries | Local adapter verified | PostgreSQL/object-store backup and restore drill |
| Health, redacted telemetry, and readiness | Local health/event/metric/SLO contracts and Compose assertions | Local contract only | Live service probes, tracing backend, dashboards, and alerts |
| Fault, budget, and capacity behavior | Bounded local failure/retry tests plus O10–O11 load admission contract | Partial | Provider outage, budget exhaustion, and measured deployment load thresholds |
| Security and release evidence | Secret-boundary tests, lockfile, CI/build contracts, fail-closed O12–O14 release gate, deterministic CycloneDX SBOM generation, database-backed local Trivy library scan with no unfixed findings, and a Gitleaks repository scan with no leaks | Partial | Credential rotation and production secret-scan evidence, signing, staged deployment, and signed release artifact |
| Signal & Supply | Source-bound offline fixture and deterministic rendering | Delivered locally | Live providers, deployment, publication, and authenticated UAT |

Overall status: the product foundation and local reference/demo evidence are
substantial, but production readiness is **not established**. The hard release
gaps are live provider-ASR fidelity, hosted durable-service recovery, MinIO
publication, authenticated UAT, and runtime/security/release evidence.

The latest completed local reconciliation verification passed `1978 passed, 6
skipped, 1 deselected, 70 warnings` in 48:26, with 80.09% branch coverage. The complete
Temporal-focused startup-hardening subset passed `25 passed` in 2:14. A fresh
source-bound regression set passed 99 tests in 6:13, including the API
snapshot/episode paths, source-bound workflow, runtime composition, and
long-episode mastering fix; the earlier focused API-to-Temporal integration
set passed 64 tests in 96.95s, including a real local Temporal server/worker
run. Ruff
format/check, strict mypy over 97 source files, compileall, and `git diff
--check` also passed. This is local evidence;
`uv lock --check` remains blocked by the execution environment's approval
wrapper and the venv has no pip module, so neither is represented as a pass.
The fresh host-local run and resume completed in approximately one hour; the
temporary 190,556 KiB output was moved to Trash after independent verification.
This work remains uncommitted and unpublished from the preserved reconciliation
worktree.
