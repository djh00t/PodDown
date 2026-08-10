"""Deterministic, tenant-bound MCP-style boundary for PodDown agents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
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


class ApprovalVerifier(Protocol):
    def verify_and_consume(
        self, tenant_id: str, episode_id: str, approval_id: str
    ) -> bool: ...


@dataclass
class InMemoryApprovalRegistry:
    """Deterministic demo-only approval verifier; not a production store."""

    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    approvals: dict[tuple[str, str, str], datetime] = field(default_factory=dict)
    consumed: set[tuple[str, str, str]] = field(default_factory=set)

    def issue(
        self, tenant_id: str, episode_id: str, approval_id: str, *, expires_at: datetime
    ) -> None:
        self.approvals[(tenant_id, episode_id, approval_id)] = expires_at

    def verify_and_consume(
        self, tenant_id: str, episode_id: str, approval_id: str
    ) -> bool:
        key = (tenant_id, episode_id, approval_id)
        expires_at = self.approvals.get(key)
        if expires_at is None or key in self.consumed or self.clock() >= expires_at:
            return False
        self.consumed.add(key)
        return True


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
    result_override: dict[str, object] | None = None

    def execute(
        self, tool: str, tenant_id: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        self.last_tenant = tenant_id
        if self.fail_with is not None:
            raise RuntimeError(self.fail_with)
        if self.result_override is not None:
            return self.result_override
        if tool == "poddown_preview":
            source = arguments.get("source")
            if not isinstance(source, bytes):
                raise ValueError("source must be UTF-8 bytes")
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

    def __init__(
        self,
        gateway: AgentGateway,
        context: AuthenticatedContext,
        *,
        approval_verifier: ApprovalVerifier | None = None,
    ) -> None:
        if not context.tenant_id:
            raise ValueError("authenticated tenant is required")
        self._gateway = gateway
        self._context = context
        self._approval_verifier = approval_verifier

    def tools(self) -> tuple[str, ...]:
        return TOOL_NAMES

    def schemas(self) -> dict[str, dict[str, object]]:
        output = {"type": "object", "additionalProperties": False}
        common = {
            "type": "object",
            "properties": {"episode_id": {"type": "string"}},
            "required": ["episode_id"],
            "additionalProperties": False,
            "description": "Tenant-scoped episode operation.",
            "output_schema": output,
        }
        return cast(
            dict[str, dict[str, object]],
            {
                "poddown_preview": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Markdown source."},
                        "profile": {"type": "string"},
                    },
                    "required": ["source"],
                    "additionalProperties": False,
                    "description": "Validate Markdown without paid provider work.",
                    "output_schema": output,
                },
                "poddown_render": common,
                "poddown_publish": {
                    "type": "object",
                    "properties": {
                        "episode_id": {"type": "string"},
                        "approval_id": {"type": "string"},
                    },
                    "required": ["episode_id", "approval_id"],
                    "additionalProperties": False,
                    "description": "Publish only with fresh trusted scoped approval.",
                    "output_schema": output,
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
        validation_error = self._validate_arguments(tool, arguments)
        if validation_error:
            return self._error("invalid_input", validation_error)
        if tool == "poddown_publish" and not self._trusted_approval(arguments):
            return self._error(
                "approval_required", "fresh scoped publish approval is required"
            )
        try:
            gateway_arguments = dict(arguments)
            if tool == "poddown_preview":
                gateway_arguments["source"] = str(arguments["source"]).encode("utf-8")
            result = self._safe_result(
                self._gateway.execute(tool, self._context.tenant_id, gateway_arguments)
            )
        except (KeyError, ValueError):
            return self._error("invalid_input", "PodDown tool arguments are invalid")
        except Exception:
            return self._error("internal_error", "PodDown operation failed safely")
        return {"result": result}

    def _trusted_approval(self, arguments: Mapping[str, object]) -> bool:
        if self._approval_verifier is None:
            return False
        approval_id = arguments.get("approval_id")
        episode_id = arguments.get("episode_id")
        return (
            isinstance(approval_id, str)
            and isinstance(episode_id, str)
            and self._approval_verifier.verify_and_consume(
                self._context.tenant_id, episode_id, approval_id
            )
        )

    @staticmethod
    def _validate_arguments(tool: str, arguments: Mapping[str, object]) -> str | None:
        if tool == "poddown_preview":
            return (
                None
                if isinstance(arguments.get("source"), str)
                else "source must be a Markdown string"
            )
        if tool in {"poddown_render", "poddown_get_status", "poddown_get_episode"}:
            return (
                None
                if set(arguments) == {"episode_id"}
                and isinstance(arguments.get("episode_id"), str)
                else "episode_id must be a string"
            )
        if tool == "poddown_publish":
            return (
                None
                if set(arguments) == {"episode_id", "approval_id"}
                and all(
                    isinstance(arguments.get(key), str)
                    for key in ("episode_id", "approval_id")
                )
                else "episode_id and approval_id must be strings"
            )
        return None

    @staticmethod
    def _safe_result(result: Mapping[str, object]) -> dict[str, object]:
        allowed = {
            "source_sha256",
            "profile_id",
            "side_effect",
            "job_id",
            "stage",
            "episode_id",
            "tenant_id",
            "publication_id",
            "resources",
            "manifest_sha256",
            "status",
        }
        safe = {key: value for key, value in result.items() if key in allowed}
        resources = safe.get("resources")
        if isinstance(resources, list):
            safe["resources"] = [
                {
                    "uri": item["uri"],
                    "mime_type": item.get("mime_type", "application/octet-stream"),
                }
                for item in resources
                if isinstance(item, Mapping) and isinstance(item.get("uri"), str)
            ]
        return safe

    @staticmethod
    def _error(code: str, message: str) -> dict[str, object]:
        return {"error": {"code": code, "message": message}}


__all__ = [
    "AgentMCPServer",
    "AuthenticatedContext",
    "InMemoryApprovalRegistry",
    "LocalGateway",
    "TOOL_NAMES",
]
