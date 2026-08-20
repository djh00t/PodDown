"""Unit contracts for the transport-injected HTTP agent gateway."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from pydantic import SecretStr

from poddown.http_agent_gateway import (
    AgentHttpResponse,
    HttpAgentGateway,
    HttpAgentGatewayError,
)

TENANT = "018f2c8b-7b46-7cc5-b2e1-111111111111"
PROJECT = "018f2c8b-7b46-7cc5-b2e1-222222222222"
EPISODE = "018f2c8b-7b46-7cc5-b2e1-333333333333"
DEFAULT_BEARER_TOKEN = SecretStr("test-bearer-token")


@dataclass
class _Transport:
    response: AgentHttpResponse = field(
        default_factory=lambda: AgentHttpResponse(
            status_code=200,
            body=b'{"stage":"qa","episode_id":"episode"}',
        )
    )
    calls: list[dict[str, object]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> AgentHttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


def _gateway(
    transport: _Transport,
    *,
    base_url: str = "https://api.example.test",
    bearer_token: SecretStr | None = DEFAULT_BEARER_TOKEN,
    auth_mode: str = "api",
) -> HttpAgentGateway:
    return HttpAgentGateway(
        base_url,
        bearer_token=bearer_token,
        auth_mode=auth_mode,
        transport=transport,
        timeout_seconds=7.5,
    )


def test_api_preview_uses_bearer_auth_and_does_not_forward_scope_headers() -> None:
    transport = _Transport(
        response=AgentHttpResponse(
            status_code=200,
            body=b'{"side_effect":"none"}',
        )
    )

    result = _gateway(transport).execute(
        "poddown_preview",
        TENANT,
        {"source": "# Preview", "_poddown_project_id": PROJECT},
    )

    call = transport.calls[0]
    assert result == {"side_effect": "none"}
    assert call["method"] == "POST"
    assert call["url"] == "https://api.example.test/v1/preview"
    assert json.loads(call["body"]) == {"source": "# Preview"}
    headers = call["headers"]
    assert headers["Authorization"] == "Bearer test-bearer-token"
    assert "X-Tenant-ID" not in headers
    assert "X-Project-ID" not in headers
    assert call["timeout_seconds"] == 7.5


@pytest.mark.parametrize(
    ("tool", "arguments", "method", "path"),
    [
        (
            "poddown_render",
            {"episode_id": EPISODE, "_poddown_project_id": PROJECT},
            "POST",
            f"/v1/episodes/{EPISODE}/render",
        ),
        (
            "poddown_get_status",
            {"episode_id": EPISODE, "_poddown_project_id": PROJECT},
            "GET",
            f"/v1/episodes/{EPISODE}/status",
        ),
        (
            "poddown_get_episode",
            {"episode_id": EPISODE, "_poddown_project_id": PROJECT},
            "GET",
            f"/v1/episodes/{EPISODE}",
        ),
        (
            "poddown_publish",
            {
                "episode_id": EPISODE,
                "approval_id": "approval-1",
                "target_id": "target-1",
                "_poddown_project_id": PROJECT,
            },
            "POST",
            f"/v1/episodes/{EPISODE}/publish",
        ),
    ],
)
def test_gateway_maps_tool_to_scoped_api_route(
    tool: str,
    arguments: dict[str, object],
    method: str,
    path: str,
) -> None:
    transport = _Transport()

    _gateway(transport).execute(tool, TENANT, arguments)

    call = transport.calls[0]
    assert call["method"] == method
    assert call["url"] == f"https://api.example.test{path}"
    headers = call["headers"]
    assert headers["Authorization"] == "Bearer test-bearer-token"
    assert "X-Tenant-ID" not in headers
    assert "X-Project-ID" not in headers
    if method == "POST":
        assert headers["Idempotency-Key"].startswith("mcp-")


def test_local_gateway_forwards_authenticated_compatibility_scope() -> None:
    transport = _Transport()

    _gateway(
        transport,
        base_url="http://127.0.0.1:8000",
        bearer_token=None,
        auth_mode="local",
    ).execute(
        "poddown_get_status",
        TENANT,
        {"episode_id": EPISODE, "_poddown_project_id": PROJECT},
    )

    headers = transport.calls[0]["headers"]
    assert headers["X-Tenant-ID"] == TENANT
    assert headers["X-Project-ID"] == PROJECT
    assert "Authorization" not in headers


@pytest.mark.parametrize(
    "gateway_kwargs",
    [
        {"base_url": "http://api.example.test"},
        {"base_url": "https://api.example.test/path?query=1"},
        {"auth_mode": "other"},
    ],
)
def test_gateway_rejects_unsafe_configuration(
    gateway_kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _gateway(_Transport(), **gateway_kwargs)  # type: ignore[arg-type]


def test_api_gateway_requires_bearer_token() -> None:
    with pytest.raises(ValueError, match="bearer token"):
        _gateway(_Transport(), bearer_token=None)


@pytest.mark.parametrize(
    "response",
    [
        AgentHttpResponse(status_code=400, body=b'{"detail":"secret-body"}'),
        AgentHttpResponse(status_code=502, body=b'{"detail":"secret-body"}'),
        AgentHttpResponse(status_code=200, body=b"not-json"),
        AgentHttpResponse(status_code=200, body=b"[]"),
    ],
)
def test_gateway_fails_without_returning_response_body(
    response: AgentHttpResponse,
) -> None:
    transport = _Transport(response=response)
    with pytest.raises(HttpAgentGatewayError) as error:
        _gateway(transport).execute(
            "poddown_get_status",
            TENANT,
            {"episode_id": EPISODE, "_poddown_project_id": PROJECT},
        )
    assert "secret-body" not in str(error.value)


def test_gateway_rejects_missing_project_scope() -> None:
    with pytest.raises(ValueError, match="project scope"):
        _gateway(_Transport()).execute(
            "poddown_get_status", TENANT, {"episode_id": EPISODE}
        )


def test_gateway_rejects_unknown_tool() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _gateway(_Transport()).execute("unknown", TENANT, {})
