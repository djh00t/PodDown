"""MCP resource-link authorization regressions."""

from __future__ import annotations

from uuid import UUID

from poddown.agent_mcp import AgentMCPServer, AuthenticatedContext, LocalGateway
from poddown.auth import AuthenticatedPrincipal

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")


class _Verifier:
    def __init__(self, allowed: str) -> None:
        self.allowed = allowed

    def verify(self, tenant_id: str, episode_id: str, uri: str) -> bool:
        assert tenant_id == "tenant-a"
        assert episode_id == "episode-1"
        return uri == self.allowed


def test_mcp_drops_unauthorized_resource_links() -> None:
    gateway = LocalGateway()
    gateway.episode_resources["episode-1"] = {"audio": "https://unsafe.invalid/audio"}
    server = AgentMCPServer(
        gateway,
        AuthenticatedContext("tenant-a"),
        resource_verifier=_Verifier("https://safe.invalid/audio"),
    )

    result = server.call("poddown_get_episode", {"episode_id": "episode-1"})

    assert result["result"]["resources"] == []


def test_mcp_preserves_only_verified_resource_links() -> None:
    gateway = LocalGateway()
    gateway.episode_resources["episode-1"] = {"audio": "https://safe.invalid/audio"}
    server = AgentMCPServer(
        gateway,
        AuthenticatedContext("tenant-a"),
        resource_verifier=_Verifier("https://safe.invalid/audio"),
    )

    result = server.call("poddown_get_episode", {"episode_id": "episode-1"})

    assert result["result"]["resources"][0]["uri"] == "https://safe.invalid/audio"


def test_mcp_can_derive_tenant_project_and_scopes_from_verified_principal() -> None:
    gateway = LocalGateway()
    principal = AuthenticatedPrincipal(
        issuer="https://issuer.example.test/",
        subject="operator-1",
        tenant_id=TENANT,
        project_ids=frozenset({PROJECT}),
        scopes=frozenset({"episodes:read"}),
    )
    server = AgentMCPServer.from_principal(gateway, principal)

    result = server.call("poddown_get_status", {"episode_id": "episode-1"})

    assert result["result"]["stage"] == "qa"
    assert gateway.last_tenant == str(TENANT)
    assert gateway.last_project == str(PROJECT)


def test_principal_scope_is_required_for_mcp_side_effects() -> None:
    principal = AuthenticatedPrincipal(
        issuer="https://issuer.example.test/",
        subject="reader-1",
        tenant_id=TENANT,
        project_ids=frozenset({PROJECT}),
        scopes=frozenset({"episodes:read"}),
    )
    server = AgentMCPServer.from_principal(LocalGateway(), principal)

    result = server.call("poddown_render", {"episode_id": "episode-1"})

    assert result == {
        "error": {
            "code": "scope_forbidden",
            "message": "authenticated principal lacks the required scope",
        }
    }
