# Security, retention, backup, and reproducibility contract

This M7 slice defines the local operational contract; it does not claim hosted
deployment readiness.

## Security and telemetry

- Credentials enter runtime services through environment-backed secret injection;
  secret values are never committed, logged, placed in Compose files, or emitted
  in operational events.
- Events and metrics carry tenant/project/correlation identifiers and bounded
  stage metadata only. Source text, scripts, audio bytes or paths, provider raw
  payloads, tokens, and credential-like keys are redacted.
- Tenant/project scope is required at storage and event boundaries. Authorization
  and deletion decisions are audited without retaining secret material.

## Retention and deletion

Retention must be configured independently for source snapshots, audio,
transcripts, provider payloads, logs, and consent/publication evidence. Tenant
deletion must be audited and cannot remove immutable consent or publication
evidence without an explicit legal/product retention decision.

## Backup and restore

Production requires forward-tested database migrations, PostgreSQL backup and
restore drills, object-storage checksum verification, and a recorded restore
manifest. This local slice defines the evidence shape but does not execute a
real backup, restore, hosted migration, or credentialed storage operation.

## Reproducible release evidence

The release record must include the source commit, dependency lock state,
artifact checksums, test commands, and explicit skipped external evidence.
Signing, SBOM generation, vulnerability scanning, staged deployment,
provider outage/budget/load tests, and deployment credentials remain deferred.
