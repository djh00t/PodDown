"""Deterministic, tenant-bound MCP-style boundary for PodDown agents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock
from typing import Protocol, cast
from uuid import UUID

from poddown.auth import AuthenticatedPrincipal

TOOL_NAMES = (
    "poddown_preview",
    "poddown_render",
    "poddown_publish",
    "poddown_get_status",
    "poddown_get_episode",
)

_REQUIRED_SCOPES = {
    "poddown_preview": "episodes:read",
    "poddown_render": "episodes:render",
    "poddown_publish": "episodes:publish",
    "poddown_get_status": "episodes:read",
    "poddown_get_episode": "episodes:read",
}


@dataclass(frozen=True)
class AuthenticatedContext:
    tenant_id: str
    project_id: str | None = None
    scopes: frozenset[str] | None = None
    principal: AuthenticatedPrincipal | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("authenticated tenant is required")
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not self.project_id.strip()
        ):
            raise ValueError("authenticated project is invalid")
        if self.scopes is not None and any(
            not isinstance(scope, str) or not scope.strip() for scope in self.scopes
        ):
            raise ValueError("authenticated scopes are invalid")
        if self.principal is not None:
            if self.tenant_id != str(self.principal.tenant_id):
                raise ValueError("authenticated tenant does not match principal")
            if self.project_id is None:
                raise ValueError("authenticated project is required for a principal")
            try:
                project_id = UUID(self.project_id)
            except ValueError as error:
                raise ValueError("authenticated project is invalid") from error
            if not self.principal.allows_project(project_id):
                raise ValueError("authenticated project is outside principal scope")
            if self.scopes != self.principal.scopes:
                raise ValueError("authenticated scopes do not match principal")

    @classmethod
    def from_principal(
        cls,
        principal: AuthenticatedPrincipal,
        *,
        project_id: UUID | None = None,
    ) -> AuthenticatedContext:
        """Create an MCP context from one already verified OIDC principal."""
        if not isinstance(principal, AuthenticatedPrincipal):
            raise TypeError("principal must be an AuthenticatedPrincipal")
        selected = project_id
        if selected is None:
            if len(principal.project_ids) != 1:
                raise ValueError("one project scope is required for MCP")
            selected = next(iter(principal.project_ids))
        if not principal.allows_project(selected):
            raise ValueError("project is outside principal scope")
        return cls(
            tenant_id=str(principal.tenant_id),
            project_id=str(selected),
            scopes=principal.scopes,
            principal=principal,
        )

    def has_scope(self, scope: str) -> bool:
        """Return whether this context authorizes one exact MCP operation."""
        return self.scopes is None or scope in self.scopes or "*" in self.scopes


class ApprovalVerifier(Protocol):
    def verify_and_consume(
        self, tenant_id: str, episode_id: str, approval_id: str, target_id: str
    ) -> bool: ...


class ResourceLinkVerifier(Protocol):
    """Port for checking that one returned resource link is authorized."""

    def verify(self, tenant_id: str, episode_id: str, uri: str) -> bool:
        """Return true only for a valid, non-expired, scoped resource link."""


@dataclass
class InMemoryApprovalRegistry:
    """Deterministic demo-only approval verifier; not a production store."""

    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    approvals: dict[tuple[str, str, str, str], datetime] = field(default_factory=dict)
    consumed: set[tuple[str, str, str, str]] = field(default_factory=set)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def issue(
        self,
        tenant_id: str,
        episode_id: str,
        approval_id: str,
        *,
        target_id: str,
        expires_at: datetime,
    ) -> None:
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError("target_id is required")
        self.approvals[(tenant_id, episode_id, approval_id, target_id)] = expires_at

    def verify_and_consume(
        self, tenant_id: str, episode_id: str, approval_id: str, target_id: str
    ) -> bool:
        with self._lock:
            key = (tenant_id, episode_id, approval_id, target_id)
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
    last_project: str | None = None
    fail_with: str | None = None
    episode_resources: dict[str, dict[str, str]] = field(default_factory=dict)
    result_override: dict[str, object] | None = None

    def execute(
        self, tool: str, tenant_id: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        self.last_tenant = tenant_id
        project_id = arguments.get("_poddown_project_id")
        self.last_project = project_id if isinstance(project_id, str) else None
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
        resource_verifier: ResourceLinkVerifier | None = None,
    ) -> None:
        if not context.tenant_id:
            raise ValueError("authenticated tenant is required")
        self._gateway = gateway
        self._context = context
        self._approval_verifier = approval_verifier
        self._resource_verifier = resource_verifier

    @classmethod
    def from_principal(
        cls,
        gateway: AgentGateway,
        principal: AuthenticatedPrincipal,
        *,
        project_id: object | None = None,
        approval_verifier: ApprovalVerifier | None = None,
        resource_verifier: ResourceLinkVerifier | None = None,
    ) -> AgentMCPServer:
        """Construct the production MCP boundary from verified principal scope."""
        selected_project: UUID | None
        if project_id is None:
            selected_project = None
        elif isinstance(project_id, UUID):
            selected_project = project_id
        elif isinstance(project_id, str):
            try:
                selected_project = UUID(project_id)
            except ValueError as error:
                raise ValueError("project_id is invalid") from error
        else:
            raise TypeError("project_id must be a UUID or string")
        return cls(
            gateway,
            AuthenticatedContext.from_principal(principal, project_id=selected_project),
            approval_verifier=approval_verifier,
            resource_verifier=resource_verifier,
        )

    def tools(self) -> tuple[str, ...]:
        return TOOL_NAMES

    def schemas(self) -> dict[str, dict[str, object]]:
        output = {
            "type": "object",
            "properties": {
                "source_sha256": {"type": "string"},
                "profile_id": {"type": "string"},
                "side_effect": {"type": "string"},
                "job_id": {"type": "string"},
                "stage": {"type": "string"},
                "episode_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "publication_id": {"type": "string"},
                "resources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "uri": {"type": "string"},
                            "mime_type": {"type": "string"},
                        },
                        "required": ["uri", "mime_type"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }
        output_fields = {
            "poddown_preview": {"source_sha256", "profile_id", "side_effect"},
            "poddown_render": {"job_id", "side_effect"},
            "poddown_publish": {"publication_id", "side_effect"},
            "poddown_get_status": {"episode_id", "stage"},
            "poddown_get_episode": {"episode_id", "tenant_id", "resources"},
        }
        output_properties = cast(dict[str, dict[str, object]], output["properties"])

        def scoped_output(tool: str) -> dict[str, object]:
            return {
                **output,
                "properties": {
                    key: output_properties[key] for key in output_fields[tool]
                },
            }

        common = {
            "type": "object",
            "properties": {"episode_id": {"type": "string"}},
            "required": ["episode_id"],
            "additionalProperties": False,
            "description": "Tenant-scoped episode operation.",
            "output_schema": scoped_output("poddown_get_status"),
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
                    "output_schema": scoped_output("poddown_preview"),
                },
                "poddown_render": {
                    **common,
                    "output_schema": scoped_output("poddown_render"),
                },
                "poddown_publish": {
                    "type": "object",
                    "properties": {
                        "episode_id": {"type": "string"},
                        "approval_id": {"type": "string"},
                        "target_id": {"type": "string"},
                    },
                    "required": ["episode_id", "approval_id", "target_id"],
                    "additionalProperties": False,
                    "description": "Publish only with fresh trusted scoped approval.",
                    "output_schema": scoped_output("poddown_publish"),
                },
                "poddown_get_status": common,
                "poddown_get_episode": {
                    **common,
                    "output_schema": scoped_output("poddown_get_episode"),
                },
            },
        )

    def call(self, tool: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if tool not in TOOL_NAMES:
            return self._error("unknown_tool", "unsupported PodDown tool")
        if not isinstance(arguments, Mapping):
            return self._error("invalid_input", "tool arguments must be an object")
        if "tenant_id" in arguments:
            return self._error("invalid_input", "tenant scope is server-authenticated")
        validation_error = self._validate_arguments(tool, arguments)
        if validation_error:
            return self._error("invalid_input", validation_error)
        required_scope = _REQUIRED_SCOPES[tool]
        if not self._context.has_scope(required_scope):
            return self._error(
                "scope_forbidden", "authenticated principal lacks the required scope"
            )
        try:
            if tool == "poddown_publish" and not self._trusted_approval(arguments):
                return self._error(
                    "approval_required", "fresh scoped publish approval is required"
                )
            gateway_arguments = dict(arguments)
            if self._context.project_id is not None:
                gateway_arguments["_poddown_project_id"] = self._context.project_id
            if tool == "poddown_preview":
                gateway_arguments["source"] = str(arguments["source"]).encode("utf-8")
            result = self._safe_result(
                self._gateway.execute(tool, self._context.tenant_id, gateway_arguments),
                tenant_id=self._context.tenant_id,
                episode_id=str(arguments.get("episode_id", "")),
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
        target_id = arguments.get("target_id")
        return (
            isinstance(approval_id, str)
            and isinstance(episode_id, str)
            and isinstance(target_id, str)
            and self._approval_verifier.verify_and_consume(
                self._context.tenant_id, episode_id, approval_id, target_id
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
                if set(arguments) == {"episode_id", "approval_id", "target_id"}
                and all(
                    isinstance(arguments.get(key), str)
                    for key in ("episode_id", "approval_id", "target_id")
                )
                else "episode_id, approval_id, and target_id must be strings"
            )
        return None

    def _safe_result(
        self,
        result: Mapping[str, object],
        *,
        tenant_id: str,
        episode_id: str,
    ) -> dict[str, object]:
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
        safe: dict[str, object] = {
            key: value
            for key, value in result.items()
            if key in allowed - {"resources"} and isinstance(value, str)
        }
        resources = result.get("resources")
        if isinstance(resources, list):
            safe["resources"] = [
                {
                    "uri": item["uri"],
                    "mime_type": item.get("mime_type", "application/octet-stream"),
                }
                for item in resources
                if isinstance(item, Mapping)
                and isinstance(item.get("uri"), str)
                and isinstance(item.get("mime_type", "application/octet-stream"), str)
                and (
                    self._resource_verifier is None
                    or self._resource_verifier.verify(
                        tenant_id, episode_id, item["uri"]
                    )
                )
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
    "ResourceLinkVerifier",
    "TOOL_NAMES",
]
