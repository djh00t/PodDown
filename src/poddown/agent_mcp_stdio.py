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

MCP_PROTOCOL_VERSION = "2025-11-25"


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
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            continue
        if not isinstance(request, Mapping):
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid Request"},
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            continue
        method = request.get("method")
        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "poddown", "version": "0.1.0"},
                },
            }
        elif method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": schema["description"],
                    "inputSchema": {
                        key: value
                        for key, value in schema.items()
                        if key
                        in {
                            "type",
                            "properties",
                            "required",
                            "additionalProperties",
                            "description",
                        }
                    },
                }
                for name, schema in server.schemas().items()
            ]
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {"tools": tools},
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
                arguments = params.get("arguments", {})
                result = server.call(str(params.get("name")), arguments)
                if "error" in result:
                    payload = {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result["error"], sort_keys=True),
                            }
                        ],
                    }
                else:
                    structured = result.get("result", {})
                    payload = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(structured, sort_keys=True),
                            }
                        ],
                        "structuredContent": structured,
                    }
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": payload,
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
