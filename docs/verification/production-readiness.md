# M7 production-readiness verification

## Scope

This slice adds a versioned local Compose topology for PostgreSQL, Temporal,
NATS JetStream, MinIO, API, and worker; offline health endpoints; and immutable
tenant-safe operational event/metric contracts. Kubernetes, live services, and
credentials are outside the default test boundary.

## Evidence

Focused contract tests:

```text
rtk uv run pytest -q tests/unit/test_production_contracts.py tests/unit/test_compose_contract.py tests/integration/test_health_boundary.py tests/bdd/test_production_readiness.py
11 passed
```

The changed-scope `rtk make check` completed with 816 selected tests and one
live-provider test deselected; its full output is local command evidence, not
Compose E2E evidence.

`rtk make build`, `rtk make docs`, `rtk uv lock --check`, `rtk uv pip check`,
`rtk uv run python -m compileall -q src tests`, and `rtk git diff --check`
completed successfully. The changed-file credential scan found no credential
values. Ruff formatting and focused Ruff checks also passed.

The Compose YAML is statically parsed only. A clean Compose E2E test is not
claimed unless Docker and every service are actually available.

## Explicit deferrals

- Hosted PostgreSQL/Temporal/NATS/MinIO migrations and deployment configuration.
- Real backup/restore and disaster-recovery drills.
- Provider outage, budget exhaustion, and load/capacity testing.
- Signing, SBOM, vulnerability scanning, and staged deployment.
- Deployment credentials and live provider credentials.

Docker/Compose execution was not run; service availability and the Docker
daemon were not assumed.
