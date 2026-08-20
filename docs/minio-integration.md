# MinIO integration evidence

PodDown's MinIO tests are opt-in local integration evidence. The default test
suite never contacts an object-storage endpoint and reports the integration
tests as skipped.

Start the repository's local Compose MinIO service, then provide credentials
through environment variables without committing them:

```bash
export PODDOWN_MINIO_TESTS=1
export PODDOWN_MINIO_ENDPOINT=http://127.0.0.1:9000
export PODDOWN_MINIO_ACCESS_KEY="$PODDOWN_MINIO_ROOT_USER"
export PODDOWN_MINIO_SECRET_KEY="$PODDOWN_MINIO_ROOT_PASSWORD"
uv run pytest -m minio tests/integration/test_minio_object_storage.py -q
```

The integration target is restricted to literal loopback IP addresses on the
local host. It verifies exact round trips, immutable replay, cross-project
isolation, and corruption detection. A skipped run is
not live MinIO evidence; it means the local service and explicit credentials
were not enabled.

Verified local run on 2026-08-16: 3 tests passed against the pinned MinIO
image in a disposable tmpfs-backed container. This is local integration
evidence; it does not prove hosted storage recovery or production readiness.
