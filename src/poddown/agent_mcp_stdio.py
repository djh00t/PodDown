"""JSON-lines MCP-compatible local entrypoint for PodDown agents."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from poddown.agent_mcp import (
    AgentMCPServer,
    AuthenticatedContext,
    InMemoryApprovalRegistry,
    LocalGateway,
)


def _server_from_environment() -> AgentMCPServer | None:
    tenant_id = os.environ.get("PODDOWN_TENANT_ID")
    if not tenant_id:
        return None
    registry = InMemoryApprovalRegistry()
    token = os.environ.get("PODDOWN_APPROVAL_TOKEN")
    episode_id = os.environ.get("PODDOWN_APPROVAL_EPISODE_ID")
    expires_at_text = os.environ.get("PODDOWN_APPROVAL_EXPIRES_AT")
    if token and episode_id and expires_at_text:
        try:
            expires_at = datetime.fromisoformat(expires_at_text).astimezone(UTC)
        except ValueError:
            expires_at = datetime.now(UTC) - timedelta(seconds=1)
        registry.issue(tenant_id, episode_id, token, expires_at=expires_at)
    return AgentMCPServer(
        LocalGateway(), AuthenticatedContext(tenant_id), approval_verifier=registry
    )


def main() -> int:
    server = _server_from_environment()
    if server is None:
        print("authenticated tenant context is required", file=sys.stderr)
        return 2
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        if method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {"tools": server.schemas()},
            }
        elif method == "tools/call":
            params = request.get("params")
            if not isinstance(params, Mapping):
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {"code": "invalid_input", "message": "invalid tool call"},
                }
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    **server.call(str(params.get("name")), params.get("arguments", {})),
                }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": "method_not_found",
                    "message": "unsupported MCP method",
                },
            }
        print(json.dumps(response, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
