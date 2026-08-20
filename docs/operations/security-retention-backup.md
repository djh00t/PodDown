# Security, retention, backup, and reproducibility contract

The local operational contract is covered by the [O06–O09 verification
record](../verification/operational-lifecycle.md); it does not claim hosted
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

The implementation treats a legal hold as an unconditional deletion block and
requires an authorization identity and reason before an expired immutable
evidence item becomes eligible. It verifies the decision before a caller may
perform a mutation.

## Backup and restore

Production requires forward-tested database migrations, PostgreSQL backup and
restore drills, object-storage checksum verification, and a recorded restore
manifest. This local slice defines the evidence shape but does not execute a
real backup, restore, hosted migration, or credentialed storage operation.

The local backup/export manifest binds scoped artifact names, media types, byte
counts, exact SHA-256 values, and a deterministic manifest SHA-256. Cross-tenant,
changed, missing, and duplicate artifacts fail verification.

## Reproducible release evidence

The release record must include the source commit, dependency lock state,
artifact checksums, test commands, and explicit skipped external evidence.
Signing, SBOM generation, vulnerability scanning, staged deployment,
provider outage/budget/load tests, and deployment credentials remain deferred.
