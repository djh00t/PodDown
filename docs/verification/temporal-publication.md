# Temporal publication activity verification

The publication boundary now has a registered `poddown.audio.publish_episode`
activity. The API carries a compact package/version reference, separately
recorded manifest digest, tenant-scoped target ID, approval decision, and
idempotency key. The worker resolves the package manifest and target from its
own stores, verifies the canonical manifest digest, and delegates exact artifact
reads and receipt creation to `PublishingService`. Package bytes, target
configuration, and secrets are not serialized into the Temporal command.

## Local evidence

The BDD acceptance test starts Temporal's local test environment and a worker with
the real command workflow and publication activity:

```text
env PYDANTIC_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src \
  pytest -p pytest_bdd.plugin tests/bdd/test_temporal_publication.py -q
```

Observed result: one scenario passed. The filesystem adapter received the exact
verified package bytes and returned one immutable publication receipt. The
runtime worker registers both the render and publication activities.

The external Transistor adapter is transport-injected for tests. Its standard
library network transport requires an explicit live opt-in; the guarded contract
tests use no network and no provider credential.

The worker target registry is an explicit `PODDOWN_PUBLICATION_TARGETS_JSON`
configuration containing only tenant/project/target metadata and `secret://`
references. If it is absent or does not contain the requested target, the
publication activity fails closed. The generic package-byte digest remains
distinct from the manifest digest; this slice does not relabel either value.

## Evidence boundary

This is local Temporal/activity evidence only. It does not establish a hosted
Temporal deployment, PostgreSQL-backed publication receipt durability, MinIO
publication, authenticated API-to-worker UAT, or external Transistor publication.
Those require a running deployment, configured non-secret secret references,
fresh approval, and separate operational evidence.
