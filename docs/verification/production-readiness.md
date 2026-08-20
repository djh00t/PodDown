# Production-readiness boundary verification

## Scope

The current local overlay contains a versioned Compose topology for
PostgreSQL, Temporal, NATS JetStream, MinIO, API, and worker services;
provider-free health/readiness contracts; redacted operational event and metric
contracts; explicit runtime configuration; and fail-closed release evidence.
The topology is a local integration target, not proof of deployed production
readiness.

## Local evidence

- The full non-live regression passed `1999 passed, 11 skipped, 2 deselected, 14
  compatibility warnings` at `80.05%` branch coverage.
- `docker compose config --quiet` passed with explicit local-only database,
  object-store, resource-link, and service endpoint values.
- A fresh local Compose run on 2026-08-16 started PostgreSQL, Temporal, NATS,
  MinIO, API, and worker services healthy; the MinIO initialization job exited
  successfully. An API create command then completed the host-local production
  workflow through Temporal and package completion. The API status response
  reported `packaged`, progress `0.9`, and manifest SHA-256
  `cd7fb13afcbb30d97aa4dc40e69e506800a99891a340f46828ac26810c8de15e`; the
  worker's canonical package-manifest calculation matched that digest. The
  run used zero provider cost and no external publication.
- The Compose/Dockerfile and worker lifecycle contracts are covered by the
  repository test suite; missing Temporal, database, authentication, and
  resource-link configuration fails closed.
- A disposable Compose restart drill accepted a real API command, restarted
  the API and worker while the Temporal status was `rendering`, and recovered
  to `packaged` with progress `0.9` and manifest SHA-256
  `32a9ebebbb342b97cdd7c7a837d0a36d6482af6bf52c76bff28c15c85765472b`. The
  drill also verified that concurrent API/worker PostgreSQL bootstrap is
  serialized by a transaction-scoped advisory lock. Its project and volumes
  were removed after the run.
- A fresh CycloneDX 1.5 SBOM contains 60 locked components.
- Gitleaks scanned approximately 35.20 MB with no leaks.
- Trivy library scanning exited `0` with no unfixed findings.
- The controlled live-provider procedure is documented in the [live-provider
  runbook](live-provider-runbook.md); it has not been activated.

These are local contract, build, and scanner results. No external credentials,
live provider request, external publication, or hosted service was used.

## Explicit evidence boundary

The Compose results are disposable local cross-process evidence only. They do
not prove hosted deployment, hosted restart/recovery, production capacity,
provider-live fidelity, or authenticated customer use. The temporary local
Compose stacks and their volumes were removed after verification.

The following remain required before production readiness can be claimed:

- hosted PostgreSQL/MinIO/NATS/Temporal migrations, isolation, and
  restart/recovery evidence beyond the disposable local service and transport
  smokes;
- credentialed ElevenLabs/OpenAI provider-ASR execution with complete evidence
  and 100% critical-token fidelity;
- deployed OIDC key/issuer validation and authenticated customer UAT;
- backup/restore, outage/load/capacity, telemetry/SLO, signing, and staged
  deployment evidence.
