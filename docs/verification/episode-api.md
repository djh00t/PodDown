# Episode API transport verification

## Scope

This slice implements the deterministic offline HTTP boundary for Spec 004.
It composes the reviewed tenant-scoped lifecycle service with an in-memory
command dispatcher and exposes the five versioned routes:

- `POST /v1/episodes`
- `GET /v1/episodes/{id}`
- `GET /v1/episodes/{id}/status`
- `POST /v1/episodes/{id}/render`
- `POST /v1/episodes/{id}/publish`

The demo boundary requires `X-Tenant-ID`, `X-Project-ID`, and
`Idempotency-Key` headers for requests that mutate state. It accepts JSON source
and profile input, returns source-safe summaries and replayable command
receipts, hides cross-tenant and cross-project records, and keeps publish
authorization and pre-package lifecycle gates explicit.

This is not production persistence or authentication. PostgreSQL/Alembic,
S3-compatible storage, NATS outbox/events, Temporal submission, real auth,
provider dispatch, metering persistence, restart recovery, and signed artifact
URLs remain subsequent slices.

## TDD evidence

The delegated test-only branch first captured the expected RED state before the
API package existed:

```text
uv run pytest -q tests/unit/test_api_models.py
ERROR collecting tests/unit/test_api_models.py
ModuleNotFoundError: No module named 'poddown.api'
```

The test contract was then integrated from commit `9571f09` and implemented
without weakening its tenant, idempotency, redaction, or publish-gate checks.

## Dependency decisions

The conservative dependency advisor selected:

- `fastapi==0.139.0` for the production HTTP transport.
- `httpx2==2.5.0` for Starlette's supported deterministic `TestClient` path.

An initial unbounded `httpx` recommendation selected `1.0.dev3`, but focused
collection failed because Starlette's legacy client expected `BaseTransport`.
Re-running the advisor with the compatibility bound selected `httpx==0.28.1`,
then Starlette's explicit deprecation guidance identified `httpx2` as the
supported client. The final lockfile uses `httpx2==2.5.0` and the focused suite
is warning-free.

## Verification commands and results

Focused BDD, model, and ASGI integration contract:

```bash
uv run pytest -q \
  tests/unit/test_api_models.py \
  tests/integration/test_episode_api.py \
  tests/bdd/test_episode_api.py
```

Result after edge-case and failure-redaction coverage: **41 passed**.

The changed-scope gate is run with live-provider tests excluded by the
repository Makefile:

```bash
make check
```

Final changed-scope result on reviewed PR16 parent `4181bb8542daba4a3136471f9e5bb44ba6725228`:

- **677 passed, 1 live-provider test deselected**.
- Branch-aware total coverage: **86.50%**; API transport `83%`, models `97%`,
  and command runtime `93%`.
- Ruff format/check passed.
- Strict mypy passed for 42 source files.

Additional required checks:

```bash
make build
make docs
uv lock --check
uv pip check
uv run python -m compileall -q src tests
git diff --check
```

All commands passed. The changed-scope credential scan returned no matches.
No live provider, network, database, object-storage, or credential path is used
by the API tests.

The API's in-memory idempotency namespace is tenant-wide: a same-key request
from another project is rejected as a conflict rather than rebound to a second
resource. The regression is covered by
`test_create_idempotency_rejects_cross_project_key_rebinding`.

## PR17 review-feedback verification

The PR17 feedback regressions were written first. Before the implementation,
the focused command reported four expected failures: the status response lacked
`version`, command receipts conflicted on a shared key across commands, a
published episode returned `409` instead of replaying its accepted render
receipt, and the matching BDD scenario could not read `version`.

After the minimal transport and dispatcher changes, the focused regression
selection passed:

```bash
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest \
  -p pytest_bdd.plugin -p pytest_cov.plugin -q \
  tests/unit/test_api_models.py tests/unit/test_api_runtime.py \
  tests/integration/test_episode_api.py tests/bdd/test_episode_api.py
```

Result: **44 passed**.

Fresh repository-owned validation then passed:

```bash
make check
make build
make docs
rtk proxy uv lock --check
rtk proxy uv pip check
rtk proxy uv run python -m compileall -q src tests
git diff --check
```

`make check` result: **680 passed, 1 live-provider test deselected**, total
branch coverage **86.54%**; Ruff format/check and strict mypy passed. Build
produced both sdist and wheel, documentation generated successfully, the lock
was current, installed packages were compatible, compileall and diff checks
passed. A credential-pattern scan of the changed diff returned no matches.

The command receipt identity is now exactly tenant, episode, command, and
idempotency key. Render first performs a non-mutating replay lookup; only a
new request reaches the published-state gate.
