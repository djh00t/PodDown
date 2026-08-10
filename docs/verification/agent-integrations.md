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

## Focused evidence

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
uv run pytest -p pytest_bdd.plugin -q \
  tests/bdd/test_agent_integrations.py \
  tests/unit/test_agent_mcp.py \
  tests/integration/test_agent_mcp.py \
  tests/contract/test_agent_mcp_contract.py
13 passed
```

The initial RED run failed during collection with four `ModuleNotFoundError`
errors for the intentionally absent `poddown.agent_mcp` module. A subsequent
test-harness correction exposed and fixed a ScenarioContext misuse; the final
focused run passes.

## Deferrals and residual risks

- This is a transport-neutral local MCP boundary, not a network MCP transport.
- The injected local gateway is deterministic evidence, not a claim of live API,
  Temporal, provider, or publication readiness.
- API schema reuse and real authenticated context wiring require coordinator-only
  integration with the existing API/runtime boundary; this slice does not edit
  those shared files.
- The versioned skill is repository-local and is not published or registered.
