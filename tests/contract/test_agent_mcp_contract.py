from __future__ import annotations

from poddown.agent_mcp import AgentMCPServer, AuthenticatedContext, LocalGateway


def test_tool_schemas_have_no_tenant_input_and_publish_has_approval():
    schemas = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a")).schemas()
    for _name, schema in schemas.items():
        assert "tenant_id" not in schema["properties"]
    assert "approval" in schemas["poddown_publish"]["properties"]


def test_error_contract_is_json_safe_and_redacted():
    gateway = LocalGateway()
    gateway.fail_with = "source=PRIVATE credential=SECRET voice_id=V1"
    result = AgentMCPServer(gateway, AuthenticatedContext("tenant-a")).call(
        "poddown_get_status", {"episode_id": "episode-1"}
    )
    assert result == {
        "error": {
            "code": "internal_error",
            "message": "PodDown operation failed safely",
        }
    }
