# Agent integrations verification

## Scope

This slice adds a deterministic local MCP-style boundary and versioned PodDown
skill/evals. The boundary exposes `poddown_preview`, `poddown_render`,
`poddown_publish`, `poddown_get_status`, and `poddown_get_episode` through an
authenticated tenant context. It delegates business work to an injected
gateway, rejects caller-supplied tenant scope, requires fresh episode-scoped
publish approval, and returns redacted errors and audio resource links.

The v1 skill teaches preview-before-render, source preservation, side-effect
distinctions, and refusal of unauthorized publication. `skills/poddown/v1/evals.json`
is deterministic fixture data; it does not call providers or external services.

Review corrections add a trusted injected approval registry with tenant and
episode scope, trusted-clock expiry, and one-time consumption. Model-supplied
`fresh` flags and nonces are ignored. The stdio entrypoint reads only
`PODDOWN_TENANT_ID`; absent authentication exits with code 2.

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
26 passed
```

The final review-fix RED run failed in the trusted-environment stdio publish
test because approval registration was absent. The final focused run passes
26 scenarios/tests, including the two-thread one-time-consumption regression.

The stdio process registers only a trusted environment token/configuration:
`PODDOWN_APPROVAL_TOKEN`, `PODDOWN_APPROVAL_EPISODE_ID`, and
`PODDOWN_APPROVAL_EXPIRES_AT`. The token is never printed. Model-supplied
fresh/nonce fields remain non-authoritative.

## Final checks

The changed-scope `make check` passed with **774 tests passed, 1 live-provider
test deselected**, and **86.34%** branch coverage. Ruff, strict mypy, build,
docs, lock, pip, compile, diff, and credential checks also passed. No
`check-full` or `quality-gates` run was performed locally.

## Deferrals and residual risks

- This is a transport-neutral local MCP boundary, not a network MCP transport.
- The stdio JSON-lines entrypoint is MCP-compatible local plumbing, not a claim
  of production transport, authorization middleware, or deployment readiness.
- The injected local gateway is deterministic evidence, not a claim of live API,
  Temporal, provider, or publication readiness.
- API schema reuse and real authenticated context wiring require coordinator-only
  integration with the existing API/runtime boundary; this slice does not edit
  those shared files.
- The versioned skill is repository-local and is not published or registered.
