from __future__ import annotations

from poddown.agent_mcp import AgentMCPServer, AuthenticatedContext, LocalGateway


def test_tool_schemas_have_no_tenant_input_and_publish_has_approval():
    schemas = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a")).schemas()
    for _name, schema in schemas.items():
        assert "tenant_id" not in schema["properties"]
    assert "approval_id" in schemas["poddown_publish"]["properties"]


def test_schema_shaped_calls_cover_every_tool():
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a"))
    calls = {
        "poddown_preview": {"source": "# hi"},
        "poddown_render": {"episode_id": "episode-1"},
        "poddown_publish": {
            "episode_id": "episode-1",
            "approval_id": "a1",
            "target_id": "target-1",
        },
        "poddown_get_status": {"episode_id": "episode-1"},
        "poddown_get_episode": {"episode_id": "episode-1"},
    }
    for name, arguments in calls.items():
        result = server.call(name, arguments)
        assert "error" not in result or result["error"]["code"] == "approval_required"


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


def test_each_output_schema_is_closed_and_declares_properties():
    schemas = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a")).schemas()
    for schema in schemas.values():
        output = schema["output_schema"]
        assert output["type"] == "object"
        assert output["additionalProperties"] is False
        assert output["properties"]
