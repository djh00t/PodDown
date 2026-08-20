from __future__ import annotations

from datetime import UTC

from poddown.agent_mcp import (
    TOOL_NAMES,
    AgentMCPServer,
    AuthenticatedContext,
    InMemoryApprovalRegistry,
    LocalGateway,
)


def test_exposes_the_versioned_tool_boundary():
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a"))
    assert server.tools() == TOOL_NAMES


def test_render_is_distinct_from_free_preview():
    gateway = LocalGateway()
    server = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))
    assert (
        server.call("poddown_preview", {"source": "# A"})["result"]["side_effect"]
        == "none"
    )
    assert (
        server.call("poddown_render", {"episode_id": "episode-1"})["result"][
            "side_effect"
        ]
        == "paid_async"
    )
    assert gateway.side_effects == ["render"]


def test_publish_accepts_only_fresh_matching_approval():
    gateway = LocalGateway()
    registry = InMemoryApprovalRegistry()
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    registry.issue(
        "tenant-a",
        "episode-1",
        "approval-1",
        target_id="target-1",
        expires_at=now + timedelta(minutes=1),
    )
    server = AgentMCPServer(
        gateway, AuthenticatedContext("tenant-a"), approval_verifier=registry
    )
    result = server.call(
        "poddown_publish",
        {
            "episode_id": "episode-1",
            "approval_id": "approval-1",
            "target_id": "target-1",
        },
    )
    assert result["result"]["side_effect"] == "external_publish"
    assert gateway.side_effects == ["publish"]


def test_publish_requires_target_scoped_approval():
    gateway = LocalGateway()
    registry = InMemoryApprovalRegistry()
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    registry.issue(
        "tenant-a",
        "episode-1",
        "approval-1",
        target_id="target-1",
        expires_at=now + timedelta(minutes=1),
    )
    server = AgentMCPServer(
        gateway, AuthenticatedContext("tenant-a"), approval_verifier=registry
    )

    valid = server.call(
        "poddown_publish",
        {
            "episode_id": "episode-1",
            "approval_id": "approval-1",
            "target_id": "target-1",
        },
    )
    wrong_target = server.call(
        "poddown_publish",
        {
            "episode_id": "episode-1",
            "approval_id": "approval-1",
            "target_id": "target-2",
        },
    )

    assert valid["result"]["side_effect"] == "external_publish"
    assert wrong_target["error"]["code"] == "approval_required"


def test_publish_schema_requires_target_id():
    schema = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a")).schemas()[
        "poddown_publish"
    ]

    assert "target_id" in schema["properties"]
    assert set(schema["required"]) == {"episode_id", "approval_id", "target_id"}


def test_unknown_tool_has_stable_error():
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a"))
    assert server.call("unknown", {}) == {
        "error": {"code": "unknown_tool", "message": "unsupported PodDown tool"}
    }
