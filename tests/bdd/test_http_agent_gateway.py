"""BDD coverage for the authenticated HTTP agent gateway boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr
from pytest_bdd import given, scenarios, then, when

from poddown.http_agent_gateway import AgentHttpResponse, HttpAgentGateway

scenarios("../features/http_agent_gateway.feature")


@dataclass
class _Transport:
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
        return AgentHttpResponse(
            status_code=200,
            body=json.dumps(
                {
                    "source_sha256": "a" * 64,
                    "profile_id": "default",
                    "side_effect": "none",
                }
            ).encode("utf-8"),
        )


@given("an API agent gateway transport")
def api_gateway_transport(context: Any) -> None:
    transport = _Transport()
    context.values["transport"] = transport
    context.values["gateway"] = HttpAgentGateway(
        "https://api.example.test",
        bearer_token=SecretStr("test-bearer-token"),
        transport=transport,
    )


@when("the gateway executes an API preview")
def execute_api_preview(context: Any) -> None:
    context.values["result"] = context.values["gateway"].execute(
        "poddown_preview",
        "018f2c8b-7b46-7cc5-b2e1-111111111111",
        {
            "source": "# Source-bound preview",
            "_poddown_project_id": "018f2c8b-7b46-7cc5-b2e1-222222222222",
        },
    )


@then("the request uses bearer authentication without tenant headers")
def assert_api_scope_boundary(context: Any) -> None:
    call = context.values["transport"].calls[0]
    headers = call["headers"]
    assert headers["Authorization"] == "Bearer test-bearer-token"
    assert "X-Tenant-ID" not in headers
    assert "X-Project-ID" not in headers
    assert call["method"] == "POST"
    assert call["url"] == "https://api.example.test/v1/preview"
    assert json.loads(call["body"]) == {"source": "# Source-bound preview"}
    assert context.values["result"]["side_effect"] == "none"
