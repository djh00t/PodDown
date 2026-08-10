from __future__ import annotations

from datetime import UTC, datetime, timedelta

from poddown.agent_mcp import (
    AgentMCPServer,
    AuthenticatedContext,
    InMemoryApprovalRegistry,
    LocalGateway,
)


def test_approval_registry_requires_trusted_one_time_time_bounded_approval():
    now = datetime(2026, 8, 10, tzinfo=UTC)
    registry = InMemoryApprovalRegistry(clock=lambda: now)
    registry.issue(
        "tenant-a", "episode-1", "approval-1", expires_at=now + timedelta(minutes=5)
    )
    server = AgentMCPServer(
        LocalGateway(), AuthenticatedContext("tenant-a"), approval_verifier=registry
    )
    valid = {"episode_id": "episode-1", "approval_id": "approval-1"}
    assert (
        server.call("poddown_publish", valid)["result"]["side_effect"]
        == "external_publish"
    )
    assert server.call("poddown_publish", valid)["error"]["code"] == "approval_required"


def test_stale_tampered_and_cross_tenant_approvals_fail_closed():
    now = datetime(2026, 8, 10, tzinfo=UTC)
    registry = InMemoryApprovalRegistry(clock=lambda: now)
    registry.issue(
        "tenant-a", "episode-1", "approval-1", expires_at=now - timedelta(seconds=1)
    )
    server = AgentMCPServer(
        LocalGateway(), AuthenticatedContext("tenant-a"), approval_verifier=registry
    )
    for args in (
        {"episode_id": "episode-1", "approval_id": "approval-1"},
        {"episode_id": "episode-2", "approval_id": "approval-1"},
        {"episode_id": "episode-1", "approval_id": "missing"},
    ):
        assert (
            server.call("poddown_publish", args)["error"]["code"] == "approval_required"
        )


def test_default_verifier_is_fail_closed_and_model_flags_are_not_authority():
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a"))
    args = {"episode_id": "episode-1", "approval_id": "model-made"}
    assert server.call("poddown_publish", args)["error"]["code"] == "approval_required"


def test_tool_validation_and_result_allowlist_reject_malicious_gateway_output():
    gateway = LocalGateway()
    gateway.result_override = {
        "source": "PRIVATE",
        "credential": "SECRET",
        "voice_id": "VOICE",
        "provider_payload": {"token": "TOKEN"},
        "status": "ready",
    }
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a"))
    server._gateway = gateway
    result = server.call("poddown_get_status", {"episode_id": "episode-1"})
    assert "PRIVATE" not in str(result)
    assert "SECRET" not in str(result)
    assert "VOICE" not in str(result)
    assert "TOKEN" not in str(result)
    assert result["result"]["status"] == "ready"
    assert server.call("poddown_render", {})["error"]["code"] == "invalid_input"
    assert (
        server.call("poddown_preview", {"source": 1})["error"]["code"]
        == "invalid_input"
    )


def test_schema_declares_required_fields_descriptions_outputs_and_string_source():
    schemas = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a")).schemas()
    for schema in schemas.values():
        assert schema["description"]
        assert schema["output_schema"]
    assert schemas["poddown_preview"]["properties"]["source"]["type"] == "string"
    assert "source" in schemas["poddown_preview"]["required"]
    assert "episode_id" in schemas["poddown_render"]["required"]


def test_output_schemas_declare_the_fields_returned_by_each_tool():
    schemas = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a")).schemas()
    expected = {
        "poddown_preview": {"source_sha256", "profile_id", "side_effect"},
        "poddown_render": {"job_id", "side_effect"},
        "poddown_publish": {"publication_id", "side_effect"},
        "poddown_get_status": {"episode_id", "stage"},
        "poddown_get_episode": {"episode_id", "tenant_id", "resources"},
    }
    for tool, properties in expected.items():
        assert set(schemas[tool]["output_schema"]["properties"]) == properties


def test_nested_allowed_values_are_shape_checked_and_redacted():
    gateway = LocalGateway(result_override={"status": {"credential": "SECRET"}})
    server = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))
    result = server.call("poddown_get_status", {"episode_id": "episode-1"})
    assert result["result"] == {}
    assert "SECRET" not in str(result)


def test_approval_verifier_failure_is_a_stable_redacted_error():
    class BrokenVerifier:
        def verify_and_consume(self, tenant_id, episode_id, approval_id):
            raise RuntimeError("credential=SECRET backing store unavailable")

    server = AgentMCPServer(
        LocalGateway(),
        AuthenticatedContext("tenant-a"),
        approval_verifier=BrokenVerifier(),
    )
    assert server.call(
        "poddown_publish", {"episode_id": "episode-1", "approval_id": "a1"}
    ) == {
        "error": {
            "code": "internal_error",
            "message": "PodDown operation failed safely",
        }
    }
