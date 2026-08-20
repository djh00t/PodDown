# Agent integrations verification

## Scope

This slice adds a deterministic local MCP-style boundary and versioned PodDown
skill/evals. The boundary exposes eight tools through an authenticated tenant
context: `poddown_preview`, `poddown_render`, `poddown_publish`,
`poddown_get_status`, `poddown_get_episode`, `poddown_get_audio`,
`poddown_get_transcript`, and `poddown_get_manifest`. It delegates business
work to an injected gateway, rejects caller-supplied tenant scope, requires
fresh episode-scoped publish approval, and returns redacted errors and resource
links.

The v1 skill teaches preview-before-render, source preservation, side-effect
distinctions, and refusal of unauthorized publication. `skills/poddown/v1/evals.json`
is deterministic fixture data; it does not call providers or external services.

Review corrections add a trusted injected approval registry with tenant and
episode scope, trusted-clock expiry, and one-time consumption. Model-supplied
`fresh` flags and nonces are ignored.

The stdio entrypoint requires `PODDOWN_MCP_MODE=local` or `PODDOWN_MCP_MODE=api`.
Local mode requires `PODDOWN_TENANT_ID`. API mode requires
`PODDOWN_API_ENDPOINT`, `PODDOWN_TENANT_ID`, `PODDOWN_PROJECT_ID`, and a
runtime-provisioned `PODDOWN_API_TOKEN` reference/value for an OIDC-compatible
bearer access token. It forwards that value only as `Authorization: Bearer`
to the Episode API; it never treats tenant/project context as authentication,
and it fails closed with exit code 2 when required API configuration is absent.
Do not put the token in MCP tool arguments, checked-in configuration, logs, or
verification output.

## Focused evidence

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
uv run pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_agent_integrations.py \
  tests/unit/test_agent_mcp.py \
  tests/unit/test_agent_mcp_review_regressions.py \
  tests/unit/test_agent_mcp_concurrency.py \
  tests/unit/test_http_agent_gateway.py \
  tests/integration/test_agent_mcp.py \
  tests/integration/test_agent_mcp_stdio.py \
  tests/contract/test_agent_mcp_contract.py
95 passed
```

The M07 review-fix RED run showed the authenticated API-stdio scenario lacked
render and status dispatches. The focused run passes 95 scenarios/tests,
including auth-required API dispatch, unauthorized-result redaction,
authenticated API-stdio dispatch for all eight tools, and the two-thread
one-time-consumption regression.

The stdio process registers only a trusted environment token/configuration:
`PODDOWN_APPROVAL_TOKEN`, `PODDOWN_APPROVAL_EPISODE_ID`, and
`PODDOWN_APPROVAL_EXPIRES_AT`. The token is never printed. Model-supplied
fresh/nonce fields remain non-authoritative.

The MCP transport now negotiates protocol version `2025-11-25`, advertises the
tools capability, returns `tools/list` as an array of named tool objects, and
wraps calls in `CallToolResult` content plus `structuredContent`. Tool output
schemas declare closed per-tool properties; nested result values are retained
only when their declared shape is safe. Approval-verifier exceptions and
non-object arguments return stable redacted failures without terminating stdio.

## Final checks

The changed-scope `make check` passed with **782 tests passed, 1 live-provider
test deselected**, and **86.38%** branch coverage. Ruff, strict mypy, build,
docs, lock, pip, compile, diff, and credential checks also passed. No
`check-full` or `quality-gates` run was performed locally.

## Deferrals and residual risks

- This is a transport-neutral local MCP boundary, not a network MCP transport.
- The stdio JSON-lines entrypoint is MCP-compatible local plumbing, not a claim
  of production transport, authorization middleware, or deployment readiness.
- The injected local gateway is deterministic evidence, not a claim of live API,
  Temporal, provider, or publication readiness.
- API mode forwards a configured bearer token but does not mint, refresh, or
  validate OIDC tokens locally; the verified Episode API remains the
  authentication authority.
- The versioned skill is repository-local and is not published or registered.
