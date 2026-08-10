# Publishing verification

## Scope

This slice adds an offline-safe publishing port and immutable receipt for
verified `EpisodePackage` values. It includes staged filesystem publication,
tenant-scoped ObjectStore-backed S3-compatible semantics, deterministic RSS
history, separately authorized filesystem update/delete mutations, and a
recorded-fixture Transistor contract adapter.

The publisher rejects failed QA, binds tenant/project/package checksum,
authorization, disclosure, and adapter provenance into the receipt, and replays
one receipt for a tenant/project/idempotency key. Failed adapter attempts retain
process-local retry state; retry resumes with one receipt and no duplicate feed
  entry. Filesystem publication stages all bytes before commit and cleans failed
  staging. ObjectStore publication promotes references only after every artifact
  succeeds. RSS retains multiple GUID-keyed items in stable order and rejects
  conflicting same-GUID content.

The S3-compatible adapter preflights exact tenant/project content-addressed
references, serializes attempts, and deletes only references created by a
failed attempt through `ObjectStore.delete`. Reused immutable objects remain
untouched. Failed attempts clear staged/final mappings; underlying blob GC and
cross-process transaction durability remain deferred.

## RED evidence

Before `src/poddown/publishing.py` existed:

```text
3 errors during collection
ModuleNotFoundError: No module named 'poddown.publishing'
```

## Focused verification

```bash
uv run pytest -q \
  tests/unit/test_publishing_contracts.py \
  tests/unit/test_publishing_service.py \
  tests/unit/test_publishing_adapters.py \
  tests/integration/test_publishing.py \
  tests/contract/providers/test_transistor_publishing.py \
  tests/bdd/test_publishing.py
```

Final result for the focused publishing regression set after the idempotency,
manifest, mutation-receipt, and provenance-freezing fixes: **31 passed**.

The new regressions cover concurrent same-key dispatch serialization, complete
package/version/target replay identity, manifest rejection before adapter
dispatch, identical update/delete mutation replay, and recursive receipt
provenance immutability. The full changed-scope gate collected **811 tests**,
deselected **1 live-provider test**, selected **810 tests**, and exited 0.

Object-storage regressions cover scoped verified deletion, cross-scope rejection,
reused-object preservation, no partial object after injected failure, and exact
retry promotion.

The earlier publishing and combined-stack counts above are historical. Current
validation also passed Ruff, strict mypy, build, docs, lock, pip, compileall,
diff, and credential checks. The credential scan found no credential values;
its only matches were code/documentation references to secret handling.

## Deferrals and residual risk

- Real S3 SDK/network transport and real Transistor credentials remain
  deferred; no provider credits or live network are used here.
- Content-addressed blobs written before an ObjectStore attempt fails are not
  garbage-collected by this slice; durable orphan tracking and storage GC remain
  deferred.
- Publication receipts are process-local rather than durable across process
  restart; a production ledger/outbox is required for crash recovery.
- Filesystem update/delete are observable and represented by immutable mutation
  receipts; S3/RSS mutations fail closed as unsupported offline, while
  Transistor mutations require matching recorded operation fixtures.
- Coordinator-owned `src/poddown/cli.py`, MCP/skill files, workflow files, and
  shared traceability were intentionally not changed.
