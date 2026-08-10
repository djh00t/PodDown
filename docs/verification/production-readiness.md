# M7 production-readiness verification

## Scope

This slice adds a versioned local Compose topology for PostgreSQL, Temporal,
NATS JetStream, MinIO, API, and worker; offline health endpoints; and immutable
tenant-safe operational event/metric contracts. Kubernetes, live services, and
credentials are outside the default test boundary.

## Evidence

Focused contract tests:

```text
rtk uv run pytest -q tests/unit/test_production_contracts.py tests/unit/test_compose_contract.py tests/integration/test_health_boundary.py tests/unit/test_runtime_entrypoints.py tests/bdd/test_production_readiness.py
The pre-existing focused stack was 26 passed; this review adds one Compose
regression, so the exact rerun of the command below is 27 passed, including
the worker lifecycle and API environment/readiness contracts.
```

The first correction run was intentionally RED during test-first development:
pytest collection failed with `ImportError: cannot import name
'runtime_dependency_probes' from poddown.runtime`. Production edits followed
that captured failure.

The dependency-advisor decision for the direct API server dependency was
`uvicorn==0.51.0` under the Python conservative policy with a 720-hour minimum
package age. `uv.lock` was regenerated and `uv pip check` passed.

The packaged entrypoints are `poddown-api` and `poddown-worker`. The API uses
the existing `create_app()` through uvicorn. The worker reads
`PODDOWN_TEMPORAL_ADDRESS`, `PODDOWN_TEMPORAL_NAMESPACE`, and
`PODDOWN_TEMPORAL_TASK_QUEUE`, then registers the existing
`EpisodeRenderWorkflow` and `render_segment_activity` contracts. Missing
required worker configuration fails before a network connection.

The worker marker is removed before connection, created only after the entered
Temporal worker reports `is_running`, and removed in the shutdown path. Static
Compose/Dockerfile assertions and the lifecycle test cover PATH, non-secret
Temporal environment, marker healthcheck, and cleanup wiring.

The final stacked changed-scope `rtk make check` completed with 860 passed,
one live-provider test deselected, and 86.43% coverage; its full output is
local command evidence, not Compose E2E evidence.

`rtk make build`, `rtk make docs`, `rtk uv lock --check`, `rtk uv pip check`,
`rtk uv run python -m compileall -q src tests`, and `rtk git diff --check`
completed successfully. The changed-file credential scan found no credential
values. Ruff formatting and focused Ruff checks also passed.

With Docker available, `docker build --tag poddown-m7-verify:local .` completed
successfully and installed `uvicorn==0.51.0`. No Compose services were started,
so Compose E2E and service restart evidence remain unclaimed.

The Compose YAML, Dockerfile, packaged scripts, and commands are statically
validated only. Docker/Compose E2E was not run locally, so no clean Compose E2E
or live-service readiness claim is made.

## Explicit deferrals

- Hosted PostgreSQL/Temporal/NATS/MinIO migrations and deployment configuration.
- Real backup/restore and disaster-recovery drills.
- Provider outage, budget exhaustion, and load/capacity testing.
- Signing, SBOM, vulnerability scanning, and staged deployment.
- Deployment credentials and live provider credentials.
- Kubernetes manifests or hosted migration adapters (Kubernetes is explicitly
  excluded from this local Compose contract).

Docker/Compose execution was not run; service availability and the Docker
daemon were not assumed.
