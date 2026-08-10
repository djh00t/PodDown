# M3 Tenant-Scoped Object Storage Plan

> Execute in `codex/m3-tenant-object-storage`, based on PR17's API head. Rebase
> onto the final PR17 head before publication.

## Objective

Deliver the Spec 004 object-storage port and deterministic filesystem adapter.
Every immutable object must be content-addressed, checksum-verified, and
scoped by tenant and project in both its reference and storage key. The adapter
is a local demo target; an S3-compatible implementation remains a later
integration behind the same port.

## Scope and ownership

Owned files:

- `src/poddown/object_storage.py`
- `tests/features/tenant_object_storage.feature`
- `tests/bdd/test_tenant_object_storage.py`
- `tests/unit/test_object_storage.py`
- `tests/integration/test_tenant_object_storage.py`
- `docs/verification/tenant-object-storage.md`
- this plan

Shared file requiring coordinator review:

- `docs/planning-traceability.md`

No database, API, provider, package-generation, or existing audio storage file
is modified by this slice.

## Contract decisions

- Tenant and project IDs must be UUIDv7 and are mandatory on every put/read.
- The canonical key is
  `tenants/{tenant_id}/projects/{project_id}/objects/{sha256[:2]}/{sha256}`.
  User-supplied names never become path components, so traversal and
  cross-tenant key construction are impossible.
- `ObjectRef` is immutable and records tenant, project, object name, media type,
  byte count, SHA-256, and canonical storage key.
- A same-scope put of identical bytes reuses the existing object; a corrupt or
  mismatched existing object fails closed.
- Reference metadata is persisted in a deterministic sidecar beside the object;
  reads and replays require an exact metadata match, so a caller cannot forge a
  name, media type, byte count, or storage key for existing bytes.
- Directory traversal uses descriptor-relative opens with no-follow flags for
  every canonical ancestor, and reads hash bytes from the descriptor that is
  returned to the caller.
- A read requires the caller tenant/project and exact reference scope. A
  reference from another tenant/project is rejected before filesystem access.
- Filesystem writes use a temporary sibling and atomic replacement only when
  the content-addressed destination does not already exist. Existing bytes are
  verified before reuse.

## Acceptance behaviors

1. Valid put returns a deterministic tenant/project-scoped content-addressed
   reference with exact byte count and checksum.
2. Repeating the same put is idempotent and does not mutate the existing object.
3. Different tenant/project scopes never share an object key or read result.
4. Cross-scope references, invalid UUIDs, malformed checksums, path traversal
   names, missing objects or metadata, symlinked ancestors or objects, forged
   metadata, and corrupted bytes fail with stable typed errors.
5. Media type, name, checksum, and byte count are preserved in the reference;
   object reads return exact original bytes.
6. Tests use only a temporary filesystem and do not invoke S3, network,
   credentials, providers, or cloud spend.

## TDD execution order

### Task 1: Write BDD, unit, and integration tests first

- Add BDD scenarios for put/replay, scope isolation, exact read, corruption,
  symlink traversal, exact persisted metadata, and offline-only behavior.
- Add pure unit tests for key construction, immutable reference validation, and
  typed failures.
- Add filesystem integration tests for atomic replay, metadata persistence,
  symlink traversal, missing metadata, and corruption detection.
- Capture RED collection failure because `poddown.object_storage` does not yet
  exist.

### Task 2: Implement the port and filesystem adapter

- Implement strict UUIDv7/checksum/name/media validation.
- Implement canonical tenant/project key generation.
- Implement atomic create-or-verify writes and exact-byte reads.
- Keep all external storage behind the protocol and avoid changing existing
  audio artifact semantics.

### Task 3: Verify, review, and publish

- Run focused BDD/unit/integration tests and existing artifact/package/storage
  regressions.
- Run changed-scope `make check`, build, docs, lock, dependency, compile,
  schema, credential, and clean-diff checks. Do not run `make check-full` or
  `make quality-gates` locally.
- Request independent Terra/Luna review for scope enforcement, path safety,
  immutability, corruption handling, and provider-free behavior.
- Fix every valid Important or acceptance-blocking finding, rebase onto final
  PR17, rerun verification, and create a normal ready PR directly stacked on
  PR17.

## Explicit deferrals

S3/MinIO network adapters, presigned URLs, object lifecycle/retention policy,
database references, transactional outbox events, and production restore drills
remain subsequent M3/M7 work.
