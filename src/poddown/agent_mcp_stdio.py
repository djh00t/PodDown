"""JSON-lines MCP-compatible local entrypoint for PodDown agents."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping

from poddown.agent_mcp import AgentMCPServer, AuthenticatedContext, LocalGateway


def main() -> int:
    tenant_id = os.environ.get("PODDOWN_TENANT_ID")
    if not tenant_id:
        print("authenticated tenant context is required", file=sys.stderr)
        return 2
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext(tenant_id))
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
