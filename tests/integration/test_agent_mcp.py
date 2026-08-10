from __future__ import annotations

from poddown.agent_mcp import AgentMCPServer, AuthenticatedContext, LocalGateway


def test_local_server_round_trip_uses_authenticated_context_and_safe_resources():
    gateway = LocalGateway()
    gateway.episode_resources["episode-1"] = {"audio": "https://local.invalid/audio"}
    server = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))
    result = server.call("poddown_get_episode", {"episode_id": "episode-1"})
    assert result["result"]["tenant_id"] == "tenant-a"
    assert result["result"]["resources"] == [
        {"uri": "https://local.invalid/audio", "mime_type": "audio/mpeg"}
    ]


def test_publish_rejects_stale_or_cross_tenant_approval_without_side_effect():
    gateway = LocalGateway()
    server = AgentMCPServer(gateway, AuthenticatedContext("tenant-a"))
    approval = {
        "tenant_id": "tenant-b",
        "episode_id": "episode-1",
        "nonce": "n1",
        "fresh": True,
    }
    assert (
        server.call(
            "poddown_publish", {"episode_id": "episode-1", "approval": approval}
        )["error"]["code"]
        == "approval_required"
    )
    assert gateway.side_effects == []
