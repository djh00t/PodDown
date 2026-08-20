# O06–O09 operational lifecycle verification

## Scope

This slice makes retention, tenant export, deletion authorization, and backup/
restore evidence explicit without performing destructive mutations or claiming a
hosted restore drill. Retention windows are independent for source, audio,
transcript, provider payload, log, consent, and publication evidence.

## Local contract evidence

The provider-free BDD and unit slice passed:

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_operational_lifecycle.py tests/unit/test_operations.py
16 passed
```

The contract verifies:

- expired mutable data becomes delete-eligible only at the expiry boundary;
- legal holds and immutable consent/publication evidence fail closed;
- immutable evidence deletion carries an explicit authorization identity and
  reason;
- export and backup manifests bind tenant/project scope, media type, byte count,
  exact artifact checksums, and a deterministic manifest checksum;
- changed, missing, duplicated, or cross-tenant restore artifacts are rejected.
- the local filesystem archive materializes an exact manifest and scoped byte
  set idempotently, restores it through the manifest, and rejects tampering,
  path escapes, symlinks, and unexpected entries.

## Evidence boundary

The retention module still returns decisions without deleting tenant data. The
filesystem archive is a local evidence adapter only; it does not invoke
PostgreSQL backups, write MinIO exports, or restore hosted services. Those
actions still require an authorized production adapter and a fresh recovery
drill with recorded checksums.
