# Agent Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a deterministic local MCP boundary and versioned PodDown agent skill/evals without duplicating API, lifecycle, rights, QA, or publishing rules.

**Architecture:** `poddown.agent_mcp` is a transport-neutral MCP-style server boundary with five named tools, authenticated tenant context, stable redacted errors, and resource links. It delegates to an injected application gateway rather than importing provider or CLI behavior. `skills/poddown/v1` is a versioned instruction artifact with local eval fixtures that assert tool choice, source preservation, approval refusal, and side-effect distinctions.

**Tech Stack:** Python 3.12, frozen dataclasses, JSON-compatible mappings, pytest-bdd, pytest, and deterministic local fakes. No new dependencies, paid providers, network calls, or external services.

## Global Constraints

- Tool names are `poddown_preview`, `poddown_render`, `poddown_publish`, `poddown_get_status`, and `poddown_get_episode`.
- Tenant identity comes from authenticated server context, never a model-supplied tenant ID.
- Publish requires fresh scoped approval and cannot infer approval from render consent.
- MCP failures are stable and redacted without source, voice IDs, credentials, or provider payloads.
- Audio is exposed through authorized resource links, never inline payloads.
- Offline deterministic tests are the default.
- This slice does not edit `src/poddown/cli.py`, publishing modules, workflow files, or shared traceability.

---

### Task 1: Define the executable MCP and skill contracts

**Files:**
- Create: `tests/features/agent_integrations.feature`
- Create: `tests/bdd/test_agent_integrations.py`
- Create: `tests/unit/test_agent_mcp.py`
- Create: `tests/integration/test_agent_mcp.py`
- Create: `tests/contract/test_agent_mcp_contract.py`
- Create: `skills/poddown/v1/SKILL.md`
- Create: `skills/poddown/v1/evals.json`

- [ ] Write BDD scenarios first for preview-before-render, authenticated tenant scope, fresh publish approval, redacted failures, resource links, source preservation, and side-effect distinctions.
- [ ] Run `uv run pytest -q tests/bdd/test_agent_integrations.py tests/unit/test_agent_mcp.py tests/integration/test_agent_mcp.py tests/contract/test_agent_mcp_contract.py` and record the expected missing-module RED failure.
- [ ] Add the versioned skill instructions and JSON eval cases, keeping fixtures local and provider-free.

### Task 2: Implement the smallest local MCP boundary

**Files:**
- Create: `src/poddown/agent_mcp.py`

- [ ] Define immutable tool schemas and a server-context tenant identity.
- [ ] Define an injected gateway protocol for preview, render, publish, status, and episode reads.
- [ ] Reject tenant IDs in tool arguments and reject publish without a fresh matching approval scope.
- [ ] Return JSON-safe results, stable error codes/messages, redacted failure details, and authorized resource links.
- [ ] Run the focused contract suite after each behavior is implemented.

### Task 3: Verify and document the slice

**Files:**
- Create: `docs/verification/agent-integrations.md`

- [ ] Run focused BDD/unit/integration/contract tests.
- [ ] Run changed-scope `make check`, `make build`, `make docs`, `uv lock --check`, `uv pip check`, compileall, diff check, and a credential-pattern scan.
- [ ] Confirm no forbidden files changed and record coordinator-only needs, deferrals, and residual risks.
- [ ] Create a review-ready local commit without pushing or creating a PR.

