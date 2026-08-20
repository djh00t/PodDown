"""Authenticated HTTP adapter for the transport-neutral PodDown MCP boundary."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import SecretStr
from uuid6 import uuid7

from poddown.agent_mcp import AgentGateway


class HttpAgentGatewayError(RuntimeError):
    """A redacted failure while delegating one MCP operation to the API."""


@dataclass(frozen=True, slots=True)
class AgentHttpResponse:
    """Minimal response shape required by the HTTP gateway port."""

    status_code: int
    body: bytes

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("HTTP status code is invalid")
        if not isinstance(self.body, bytes):
            raise TypeError("HTTP response body must be bytes")


class AgentHttpTransport(Protocol):
    """Injected HTTP transport used by the gateway and its contract tests."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> AgentHttpResponse:
        """Perform one bounded HTTP request without exposing credentials."""


class UrllibAgentHttpTransport:
    """Small standard-library HTTP transport for the API gateway."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> AgentHttpResponse:
        """Execute one request and normalize HTTP errors without their bodies."""
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return AgentHttpResponse(response.status, response.read())
        except HTTPError as error:
            return AgentHttpResponse(error.code, error.read())
        except (OSError, URLError) as error:
            raise HttpAgentGatewayError("API gateway request failed") from error


class HttpAgentGateway(AgentGateway):
    """Map MCP operations to the authenticated FastAPI HTTP contract."""

    _TOOLS = frozenset(
        {
            "poddown_preview",
            "poddown_render",
            "poddown_publish",
            "poddown_get_status",
            "poddown_get_episode",
        }
    )

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: SecretStr | None,
        auth_mode: str = "api",
        transport: AgentHttpTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("API gateway base URL is required")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API gateway base URL is invalid")
        if auth_mode not in {"api", "local"}:
            raise ValueError("API gateway auth mode is invalid")
        if auth_mode == "api" and parsed.scheme != "https":
            raise ValueError("API gateway API mode requires HTTPS")
        if bearer_token is not None and not isinstance(bearer_token, SecretStr):
            raise TypeError("bearer token must be a SecretStr")
        if auth_mode == "api" and (
            bearer_token is None or not bearer_token.get_secret_value()
        ):
            raise ValueError("API gateway bearer token is required")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 0 < float(timeout_seconds) <= 60
        ):
            raise ValueError("API gateway timeout must be between 0 and 60 seconds")
        if transport is not None and not hasattr(transport, "request"):
            raise TypeError("API gateway transport must implement request")
        self._base_url = base_url.rstrip("/")
        self._auth_mode = auth_mode
        self._bearer_token = bearer_token
        self._transport = transport or UrllibAgentHttpTransport()
        self._timeout_seconds = float(timeout_seconds)

    def execute(
        self,
        tool: str,
        tenant_id: str,
        arguments: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Execute one MCP tool through the scoped API route."""
        if tool not in self._TOOLS:
            raise ValueError("unsupported MCP tool")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant scope is required")
        if not isinstance(arguments, Mapping):
            raise ValueError("tool arguments must be an object")
        project_id = arguments.get("_poddown_project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project scope is required")

        method, path, payload = self._request_for_tool(tool, arguments)
        body = None
        headers = {"Accept": "application/json"}
        if self._auth_mode == "api":
            assert self._bearer_token is not None
            headers["Authorization"] = f"Bearer {self._bearer_token.get_secret_value()}"
        else:
            headers["X-Tenant-ID"] = tenant_id
            headers["X-Project-ID"] = project_id
        if method == "POST":
            headers["Idempotency-Key"] = f"mcp-{uuid7()}"
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        response = self._transport.request(
            method,
            f"{self._base_url}{path}",
            headers=headers,
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        if not 200 <= response.status_code < 300:
            raise HttpAgentGatewayError("API gateway rejected the MCP operation")
        try:
            result = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HttpAgentGatewayError("API gateway returned invalid JSON") from error
        if not isinstance(result, Mapping):
            raise HttpAgentGatewayError("API gateway returned an invalid result")
        return result

    @staticmethod
    def _request_for_tool(
        tool: str,
        arguments: Mapping[str, object],
    ) -> tuple[str, str, dict[str, object] | None]:
        if tool == "poddown_preview":
            source = arguments.get("source")
            if isinstance(source, bytes):
                try:
                    source = source.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("source must be UTF-8") from error
            if not isinstance(source, str) or not source:
                raise ValueError("source must be a non-empty string")
            profile = arguments.get("profile")
            if profile is not None and not isinstance(profile, str):
                raise ValueError("profile must be a string")
            payload: dict[str, object] = {"source": source}
            if profile is not None:
                payload["profile"] = profile
            return "POST", "/v1/preview", payload

        episode_id = arguments.get("episode_id")
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("episode_id is required")
        if tool == "poddown_render":
            return "POST", f"/v1/episodes/{episode_id}/render", {}
        if tool == "poddown_get_status":
            return "GET", f"/v1/episodes/{episode_id}/status", None
        if tool == "poddown_get_episode":
            return "GET", f"/v1/episodes/{episode_id}", None
        approval_id = arguments.get("approval_id")
        target_id = arguments.get("target_id")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id is required")
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError("target_id is required")
        return (
            "POST",
            f"/v1/episodes/{episode_id}/publish",
            {"approval_id": approval_id, "target_id": target_id},
        )


__all__ = [
    "AgentHttpResponse",
    "AgentHttpTransport",
    "HttpAgentGateway",
    "HttpAgentGatewayError",
    "UrllibAgentHttpTransport",
]
