# O06–O09 operational lifecycle verification

The local operational contract models independent retention windows for source,
audio, transcript, provider payload, logs, consent, and publication evidence.
Legal holds and immutable evidence fail closed; deletion authorization carries
an actor and reason. Export and backup manifests bind tenant/project scope,
media, byte counts, exact artifact checksums, and a deterministic manifest
checksum.

The local filesystem archive round-trip is idempotent and rejects changed,
missing, duplicated, cross-tenant, symlink, path-escape, and unexpected
artifacts. The current combined O06–O11 contract run passed 32 tests, including
16 operational-lifecycle tests.

This is a local policy/archive adapter only. It does not delete tenant data,
run PostgreSQL backups, write MinIO exports, or restore hosted services.
Authorized production mutation and a fresh recovery drill remain required.
