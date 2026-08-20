# Agent integrations verification

## Scope

This slice adds a deterministic local MCP-style boundary and versioned PodDown
skill/evals. The boundary exposes `poddown_preview`, `poddown_render`,
`poddown_publish`, `poddown_get_status`, and `poddown_get_episode` through an
authenticated tenant context. It delegates business work to an injected
gateway, rejects caller-supplied tenant scope, requires fresh
tenant/project/episode/target-scoped publish approval, and returns redacted
errors and audio resource links.

The v1 skill teaches preview-before-render, source preservation, side-effect
distinctions, and refusal of unauthorized publication. `skills/poddown/v1/evals.json`
is deterministic fixture data; it does not call providers or external services.

Review corrections add a trusted injected approval registry with tenant,
episode, and target scope, trusted-clock expiry, and one-time consumption.
Model-supplied `fresh` flags and nonces are ignored. The stdio entrypoint
requires `PODDOWN_TENANT_ID`; absent authentication exits with code 2.

## Focused evidence

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
uv run pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_agent_integrations.py \
  tests/unit/test_agent_mcp.py \
  tests/unit/test_agent_mcp_review_regressions.py \
  tests/unit/test_agent_mcp_concurrency.py \
  tests/integration/test_agent_mcp.py \
  tests/integration/test_agent_mcp_stdio.py \
  tests/contract/test_agent_mcp_contract.py
39 passed
```

The focused run passes 39 scenarios/tests, including the two-thread
one-time-consumption regression and the target-scope mismatch rejection.

The stdio process registers only a trusted environment token/configuration:
`PODDOWN_APPROVAL_TOKEN`, `PODDOWN_APPROVAL_EPISODE_ID`,
`PODDOWN_APPROVAL_TARGET_ID`, and `PODDOWN_APPROVAL_EXPIRES_AT`. The token is
never printed. Model-supplied fresh/nonce fields remain non-authoritative.

The MCP transport now negotiates protocol version `2025-11-25`, advertises the
tools capability, returns `tools/list` as an array of named tool objects, and
wraps calls in `CallToolResult` content plus `structuredContent`. Tool output
schemas declare closed per-tool properties; nested result values are retained
only when their declared shape is safe. Approval-verifier exceptions and
non-object arguments return stable redacted failures without terminating stdio.

## API gateway adapter evidence

The reconciliation worktree now contains a transport-injected
`HttpAgentGateway` for the approved API-mode boundary. Its five tool mappings
target the existing preview, render, status, episode, and publish route shapes;
API mode requires an HTTPS base URL and a `SecretStr` bearer token, derives
scope from the authenticated MCP context, and never forwards tenant/project
headers. Explicit local mode may use HTTP and forwards the compatibility
scope headers. Publish requests require both `approval_id` and `target_id` at
the API boundary.

The focused BDD/unit set passed **17 tests**. Ruff format/check, strict mypy for
the adapter, compileall, and `git diff --check` passed. The HTTP transport is
injected in these tests, so no network request was made. The provider-free
FastAPI `/v1/preview` route now has BDD/integration coverage (**4 passed**),
and MCP publish schemas/approval verification now require `target_id` as well
as `approval_id`. Signed resource retrieval, authenticated UAT, and deployed
transport evidence remain open only for the production/deployed boundary.

The combined affected agent/API suite passed **60 tests**. This is local
transport-injected evidence; it does not establish a deployed MCP transport or
authenticated production UAT.

## Signed resource evidence

The API now exposes `GET /v1/resources/{tenant}/{project}/{episode}/{kind}`
behind the authenticated `resources:read` scope. A configured
`ResourceLinkSigner` verifies the exact HTTPS origin, tenant/project/episode
scope, resource kind, expiry, media type, digest, and signature before an
injected reader is called. Returned bytes are hashed again before serving, and
missing storage or mismatched bytes fail closed without returning data. A
packaged episode summary exposes a short-lived signed manifest link; package
bytes are never relabeled as a manifest digest.

The BDD/integration resource set passed **5 tests**, and the broader API
regression set passed **50 tests**. The reader and signer are now also composed
by the PostgreSQL runtime when resource-link settings are present: the reader
resolves the signed digest through the tenant/project-scoped PostgreSQL
object-reference repository and reads exact bytes from the existing
S3-compatible store. The runtime/resource composition regression set passed
**39 tests**. A live MinIO run, deployed resource URLs, and authenticated UAT
remain pending.

## Final checks

An earlier changed-scope `make check` passed with **782 tests passed, 1
live-provider test deselected**, and **86.38%** branch coverage before this
latest resource/API slice. The latest focused suites and static checks are
reported above; no new `check-full` or `quality-gates` run was performed
locally.

## Deferrals and residual risks

- This is a transport-neutral local MCP boundary, not a network MCP transport.
- The stdio JSON-lines entrypoint is MCP-compatible local plumbing, not a claim
  of production transport, authorization middleware, or deployment readiness.
- The injected local gateway is deterministic evidence, not a claim of live API,
  Temporal, provider, or publication readiness.
- The provider-free preview and signed resource route reuse the authenticated
  FastAPI context and public source/resource contracts. Remaining MCP API-mode
  work is live MinIO verification and deployed/authenticated transport
  evidence.
- The versioned skill is repository-local and is not published or registered.
