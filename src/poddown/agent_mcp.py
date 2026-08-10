"""Deterministic, tenant-bound MCP-style boundary for PodDown agents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol, cast

TOOL_NAMES = (
    "poddown_preview",
    "poddown_render",
    "poddown_publish",
    "poddown_get_status",
    "poddown_get_episode",
)


@dataclass(frozen=True)
class AuthenticatedContext:
    tenant_id: str


class AgentGateway(Protocol):
    def execute(
        self, tool: str, tenant_id: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]: ...


@dataclass
class LocalGateway:
    """Offline gateway used by contract tests and local agent evaluation."""

    side_effects: list[str] = field(default_factory=list)
    last_tenant: str | None = None
    fail_with: str | None = None
    episode_resources: dict[str, dict[str, str]] = field(default_factory=dict)

    def execute(
        self, tool: str, tenant_id: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        self.last_tenant = tenant_id
        if self.fail_with is not None:
            raise RuntimeError(self.fail_with)
        if tool == "poddown_preview":
            source = arguments.get("source")
            if not isinstance(source, bytes):
                raise ValueError("source must be bytes")
            return {
                "source_sha256": sha256(source).hexdigest(),
                "profile_id": str(arguments.get("profile", "default")),
                "side_effect": "none",
            }
        if tool == "poddown_render":
            self.side_effects.append("render")
            return {"job_id": "job-local-1", "side_effect": "paid_async"}
        if tool == "poddown_publish":
            self.side_effects.append("publish")
            return {
                "publication_id": "publication-local-1",
                "side_effect": "external_publish",
            }
        if tool == "poddown_get_status":
            return {"episode_id": str(arguments.get("episode_id", "")), "stage": "qa"}
        if tool == "poddown_get_episode":
            episode_id = str(arguments.get("episode_id", ""))
            result: dict[str, object] = {
                "episode_id": episode_id,
                "tenant_id": tenant_id,
            }
            resource = self.episode_resources.get(episode_id, {}).get("audio")
            if resource:
                result["resources"] = [{"uri": resource, "mime_type": "audio/mpeg"}]
            return result
        raise KeyError(tool)


class AgentMCPServer:
    """Tool dispatcher that owns authentication and approval policy."""

    def __init__(self, gateway: AgentGateway, context: AuthenticatedContext) -> None:
        if not context.tenant_id:
            raise ValueError("authenticated tenant is required")
        self._gateway = gateway
        self._context = context

    def tools(self) -> tuple[str, ...]:
        return TOOL_NAMES

    def schemas(self) -> dict[str, dict[str, object]]:
        common = {
            "type": "object",
            "properties": {"episode_id": {"type": "string"}},
        }
        return cast(
            dict[str, dict[str, object]],
            {
                "poddown_preview": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "profile": {"type": "string"},
                    },
                },
                "poddown_render": common,
                "poddown_publish": {
                    "type": "object",
                    "properties": {
                        "episode_id": {"type": "string"},
                        "approval": {"type": "object"},
                    },
                },
                "poddown_get_status": common,
                "poddown_get_episode": common,
            },
        )

    def call(self, tool: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if tool not in TOOL_NAMES:
            return self._error("unknown_tool", "unsupported PodDown tool")
        if "tenant_id" in arguments:
            return self._error("invalid_input", "tenant scope is server-authenticated")
        if tool == "poddown_publish" and not self._fresh_approval(arguments):
            return self._error(
                "approval_required", "fresh scoped publish approval is required"
            )
        try:
            result = dict(
                self._gateway.execute(tool, self._context.tenant_id, arguments)
            )
        except (KeyError, ValueError):
            return self._error("invalid_input", "PodDown tool arguments are invalid")
        except Exception:
            return self._error("internal_error", "PodDown operation failed safely")
        return {"result": result}

    def _fresh_approval(self, arguments: Mapping[str, object]) -> bool:
        approval = arguments.get("approval")
        return (
            isinstance(approval, Mapping)
            and approval.get("tenant_id") == self._context.tenant_id
            and approval.get("episode_id") == arguments.get("episode_id")
            and approval.get("fresh") is True
            and isinstance(approval.get("nonce"), str)
            and bool(approval["nonce"])
        )

    @staticmethod
    def _error(code: str, message: str) -> dict[str, object]:
        return {"error": {"code": code, "message": message}}


__all__ = ["AgentMCPServer", "AuthenticatedContext", "LocalGateway", "TOOL_NAMES"]
