"""Contract tests for the injected OpenAI Responses reasoning transport."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from poddown.content.openai_reasoning import (
    OpenAIResponsesTransport,
    ReasoningResponse,
    _response_text,
    response_header_id,
)
from poddown.content.reasoning_request import ReasoningRequest
from poddown.providers.http import HttpRequest, HttpResponse, ProviderSettings


class _Transport:
    def __init__(self, response: HttpResponse):
        self.response = response
        self.requests: list[HttpRequest] = []

    async def request(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.response


def _request() -> ReasoningRequest:
    return ReasoningRequest(
        source_sha256="a" * 64,
        profile_id="technical-dialogue",
        treatment_id="treatment-1",
        operation="adapt",
        source_blocks=(
            {
                "block_id": "b1",
                "kind": "paragraph",
                "text": "source",
                "start": 0,
                "end": 6,
            },
        ),
        speaker_ids=("host", "guest"),
    )


def test_openai_transport_pins_schema_and_redacts_no_secret_from_payload() -> None:
    body = {
        "id": "resp-1",
        "model": "gpt-5-mini",
        "output_text": json.dumps(
            {
                "schema_version": "1.0",
                "source_sha256": "a" * 64,
                "treatment_id": "treatment-1",
                "model": "gpt-5-mini",
                "request_id": "resp-1",
                "turns": [
                    {
                        "turn_id": "turn-1",
                        "speaker_id": "host",
                        "kind": "factual",
                        "text": "source",
                        "source_anchors": [{"block_id": "b1", "start": 0, "end": 6}],
                        "claim_anchors": [{"block_id": "b1", "start": 0, "end": 6}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 4},
                "estimated_cost": "0.002",
            }
        ),
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
    transport = _Transport(
        HttpResponse(200, {"x-request-id": "http-1"}, json.dumps(body).encode())
    )
    adapter = OpenAIResponsesTransport(
        model="gpt-5-mini",
        settings=ProviderSettings.from_environment("KEY", {"KEY": "secret-value"}),
        transport=transport,
        cost_estimator=lambda usage: Decimal("0.002"),
    )

    result = asyncio.run(adapter.respond(_request()))

    request = transport.requests[0]
    assert request.headers["Authorization"] == "Bearer secret-value"
    assert "secret-value" not in json.dumps(request.json)
    assert request.json is not None
    assert request.json["text"]["format"]["type"] == "json_schema"
    assert result.request_id == "resp-1"
    assert result.usage.values == {"input_tokens": 3, "output_tokens": 4}
    assert result.estimated_cost == Decimal("0.002")


def test_openai_transport_uses_response_header_request_id_when_body_omits_id() -> None:
    body = {
        "model": "gpt-5-mini",
        "output_text": "{}",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    transport = _Transport(
        HttpResponse(
            200, {"x-request-id": "header-request-1"}, json.dumps(body).encode()
        )
    )
    adapter = OpenAIResponsesTransport(
        model="gpt-5-mini",
        settings=ProviderSettings.from_environment("KEY", {"KEY": "secret-value"}),
        transport=transport,
    )

    result = asyncio.run(adapter.respond(_request()))

    assert result.request_id == "header-request-1"


def test_openai_transport_advertises_complete_turn_schema() -> None:
    transport = _Transport(
        HttpResponse(
            200,
            {"x-request-id": "header-request-1"},
            json.dumps(
                {
                    "id": "resp-1",
                    "model": "gpt-5-mini",
                    "output_text": "{}",
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                }
            ).encode(),
        )
    )
    adapter = OpenAIResponsesTransport(
        model="gpt-5-mini",
        settings=ProviderSettings.from_environment("KEY", {"KEY": "secret-value"}),
        transport=transport,
    )

    asyncio.run(adapter.respond(_request()))
    assert transport.requests[0].json is not None
    schema = transport.requests[0].json["text"]["format"]["schema"]
    turn_schema = schema["properties"]["turns"]["items"]

    assert turn_schema["additionalProperties"] is False
    assert set(turn_schema["required"]) == {
        "turn_id",
        "speaker_id",
        "kind",
        "text",
        "source_anchors",
        "claim_anchors",
    }


@pytest.mark.parametrize("value", ["", None])
def test_reasoning_response_requires_identity_strings(value: object) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ReasoningResponse(value, "model", "request", {"input_tokens": 1})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty"):
        ReasoningResponse("text", value, "request", {"input_tokens": 1})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty"):
        ReasoningResponse("text", "model", value, {"input_tokens": 1})  # type: ignore[arg-type]


def test_reasoning_response_rejects_invalid_cost_and_transport_configuration() -> None:
    with pytest.raises(ValueError, match="estimated_cost"):
        ReasoningResponse(
            "text", "model", "request", {"input_tokens": 1}, Decimal("NaN")
        )
    with pytest.raises(ValueError, match="model"):
        OpenAIResponsesTransport(
            model=" ",
            settings=ProviderSettings.from_environment("KEY", {"KEY": "secret"}),
            transport=_Transport(HttpResponse(200, {}, b"{}")),
        )
    with pytest.raises(ValueError, match="settings"):
        OpenAIResponsesTransport(
            model="model",
            settings=object(),  # type: ignore[arg-type]
            transport=_Transport(HttpResponse(200, {}, b"{}")),
        )


def test_openai_transport_rejects_bad_responses_without_retrying() -> None:
    adapter = OpenAIResponsesTransport(
        model="model",
        settings=ProviderSettings.from_environment("KEY", {"KEY": "secret"}),
        transport=_Transport(HttpResponse(200, {}, b"{}")),
    )
    with pytest.raises(TypeError, match="ReasoningRequest"):
        asyncio.run(adapter.respond(object()))  # type: ignore[arg-type]
    for body in (
        b"[]",
        json.dumps({"usage": []}).encode(),
        json.dumps({"usage": {"input_tokens": "one"}}).encode(),
        json.dumps(
            {
                "usage": {"input_tokens": 1},
                "output": [{"type": "not-message"}],
            }
        ).encode(),
    ):
        with pytest.raises(ValueError, match="malformed"):
            adapter._normalize(body)


def test_openai_output_text_and_request_id_fallbacks_are_strict() -> None:
    assert response_header_id({"request_id": "body-id"}) == "body-id"
    with pytest.raises(ValueError, match="missing"):
        response_header_id({})
    valid_output = [
        {
            "type": "message",
            "content": [{"type": "output_text", "text": "structured"}],
        }
    ]
    assert _response_text(valid_output) == "structured"
    invalid_outputs = [
        [],
        [{"type": "not-message"}],
        [{"type": "message", "content": []}],
        [{"type": "message", "content": [{"type": "other"}]}],
        [{"type": "message", "content": [{"type": "output_text", "text": ""}]}],
    ]
    for value in invalid_outputs:
        with pytest.raises(ValueError):
            _response_text(value)
