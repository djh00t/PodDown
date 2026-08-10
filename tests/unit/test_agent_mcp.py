from __future__ import annotations

from poddown.agent_mcp import (
    TOOL_NAMES,
    AgentMCPServer,
    AuthenticatedContext,
    LocalGateway,
)


def test_exposes_the_versioned_tool_boundary():
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a"))
    assert server.tools() == TOOL_NAMES


def test_render_is_distinct_from_free_preview():
    gateway = LocalGateway()
    server = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))
    assert (
        server.call("poddown_preview", {"source": b"# A"})["result"]["side_effect"]
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
    server = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))
    approval = {
        "tenant_id": "tenant-a",
        "episode_id": "episode-1",
        "nonce": "n1",
        "fresh": True,
    }
    result = server.call(
        "poddown_publish", {"episode_id": "episode-1", "approval": approval}
    )
    assert result["result"]["side_effect"] == "external_publish"
    assert gateway.side_effects == ["publish"]


def test_unknown_tool_has_stable_error():
    server = AgentMCPServer(LocalGateway(), AuthenticatedContext("tenant-a"))
    assert server.call("unknown", {}) == {
        "error": {"code": "unknown_tool", "message": "unsupported PodDown tool"}
    }
