# Tenant-scoped object-storage verification

## Scope

This slice adds the Spec 004 object-storage port and deterministic local
filesystem adapter. References and canonical keys contain UUIDv7 tenant and
project scope plus a lowercase SHA-256 digest:

`tenants/{tenant_id}/projects/{project_id}/objects/{sha256[:2]}/{sha256}`

User-supplied names remain metadata and never become path components. Writes
are create-once and checksum-verified with an exact immutable metadata sidecar;
reads enforce caller scope, reject final and ancestor symlinks, verify metadata,
and hash bytes read from the same open descriptor so replacement races fail
closed.

The port also exposes scoped `delete(tenant_id, project_id, reference)`. It
verifies exact metadata and bytes before removing the object and sidecar,
rejects cross-scope references, and fails closed on unsafe paths. Publication
compensation uses this boundary; durable orphan-blob tracking and garbage
collection remain deferred.

The adapter is a local demo target only. S3/MinIO clients, presigned URLs,
retention, database references, outbox events, and restore drills remain
deferred.

## TDD evidence

The test-only contract branch captured RED before the production module was
available:

```text
uv run pytest -q tests/unit/test_object_storage.py
ERROR collecting tests/unit/test_object_storage.py
ModuleNotFoundError: No module named 'poddown.object_storage'
```

The contract was integrated from commit `b66440e` before implementation.

## Verification commands and results

Focused BDD, unit, integration, and existing artifact/storage regressions:

```bash
uv run pytest -q \
  tests/unit/test_object_storage.py \
  tests/integration/test_tenant_object_storage.py \
  tests/bdd/test_tenant_object_storage.py \
  tests/unit/test_artifacts.py \
  tests/unit/test_packages.py \
  tests/unit/audio/test_storage.py
```

Result: **64 passed**.

The final changed-scope gate includes the complete existing suite, branch-aware
coverage, Ruff, and strict mypy. Live-provider tests remain excluded by the
repository Makefile.

Additional checks:

```bash
make check
make build
make docs
uv lock --check
uv pip check
uv run python -m compileall -q src tests
git diff --check
```

The final changed-scope gate collected 706 tests, ran 705, and passed all 705
with one live-provider test deselected. Branch-aware total coverage was
**86.56%** against the repository's 80% threshold; the object-storage module
was **88%**. No provider, network, cloud storage, database, or credential path
is invoked by the storage tests.

## PR18 review-feedback verification

The new crash-recovery, concurrent replay, directory-sync, and BDD regressions
first failed: replay rejected a verified object with no metadata sidecar,
one of two identical concurrent puts failed, and the linked destination
directory was never passed to `fsync`.

After the fix, focused storage integration and BDD coverage passed **25 tests**.
That historical verification used the pre-rebase branch, which included PR17
as merge commit `6536ead`. The rebased branch's exact SHA, CI run, and fresh
validation are recorded in the PR evidence comment. Additional validation is
recorded with:

```bash
make check
make build
make docs
rtk proxy uv lock --check
rtk proxy uv pip check
rtk proxy uv run python -m compileall -q src tests
git diff --check
```

The adapter verifies object bytes before recreating only the exact metadata
sidecar, continues rejecting conflicting metadata, and fsyncs the destination
directory after each successful hard-link publication.
