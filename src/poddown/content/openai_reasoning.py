"""Injected OpenAI Responses transport for structured adaptation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from poddown.content.adaptation_envelope import AdaptationUsage
from poddown.content.reasoning_request import (
    ReasoningRequest,
    validate_reasoning_request_record,
)
from poddown.providers.http import (
    AsyncHttpTransport,
    HttpRequest,
    ProviderSettings,
    require_success,
)


@dataclass(frozen=True, slots=True)
class ReasoningResponse:
    """Normalized structured response metadata from a reasoning provider."""

    text: str
    model: str
    request_id: str
    usage: Mapping[str, int] | AdaptationUsage
    estimated_cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name, value in (
            ("text", self.text),
            ("model", self.model),
            ("request_id", self.request_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        usage = (
            self.usage
            if isinstance(self.usage, AdaptationUsage)
            else AdaptationUsage(self.usage)
        )
        if (
            not isinstance(self.estimated_cost, Decimal)
            or not self.estimated_cost.is_finite()
            or self.estimated_cost < 0
        ):
            raise ValueError("estimated_cost must be a finite non-negative Decimal")
        object.__setattr__(self, "usage", usage)


class ReasoningTransport(Protocol):
    """Async provider-neutral structured reasoning transport."""

    async def respond(self, request: ReasoningRequest) -> ReasoningResponse:
        """Return one normalized structured response."""


class OpenAIResponsesTransport:
    """Call the Responses endpoint through an injected HTTP transport."""

    def __init__(
        self,
        model: str,
        transport: AsyncHttpTransport,
        settings: ProviderSettings | None = None,
        *,
        cost_estimator: Callable[[AdaptationUsage], Decimal] | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if settings is not None and not isinstance(settings, ProviderSettings):
            raise ValueError("settings must be ProviderSettings")
        self._model = model
        self._settings = settings
        self._transport = transport
        self._cost_estimator = cost_estimator or (lambda _usage: Decimal("0"))

    async def respond(self, request: ReasoningRequest) -> ReasoningResponse:
        """Send one validated request without hidden retries or fallbacks."""
        if not isinstance(request, ReasoningRequest):
            raise TypeError("request must be ReasoningRequest")
        record = request.to_record()
        validate_reasoning_request_record(record)
        headers = {"Content-Type": "application/json"}
        timeout_seconds = 30.0
        if self._settings is not None:
            headers["Authorization"] = (
                f"Bearer {self._settings.api_key.get_secret_value()}"
            )
            timeout_seconds = self._settings.timeout_seconds
        response = await self._transport.request(
            HttpRequest(
                method="POST",
                url="https://api.openai.com/v1/responses",
                headers=headers,
                json={
                    "model": self._model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": json.dumps(
                                        record,
                                        allow_nan=False,
                                        separators=(",", ":"),
                                        sort_keys=True,
                                    ),
                                }
                            ],
                        }
                    ],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "poddown_adaptation_envelope",
                            "strict": True,
                            "schema": _adaptation_envelope_schema(),
                        }
                    },
                },
                timeout_seconds=timeout_seconds,
            )
        )
        require_success(response, "OpenAI Responses")
        return self._normalize(response.body, response.headers)

    def _normalize(
        self, body: bytes, headers: Mapping[str, str] | None = None
    ) -> ReasoningResponse:
        try:
            payload = json.loads(body)
            if not isinstance(payload, Mapping):
                raise TypeError("response must be an object")
            usage = payload.get("usage")
            if not isinstance(usage, Mapping):
                raise TypeError("response usage must be an object")
            normalized_usage = {
                str(key): value for key, value in usage.items() if type(value) is int
            }
            if len(normalized_usage) != len(usage):
                raise TypeError("response usage values must be integers")
            model = payload.get("model", self._model)
            request_id = payload.get("id") or response_header_id(payload, headers)
            text = _response_text(payload.get("output_text", payload.get("output")))
            normalized = AdaptationUsage(normalized_usage)
            return ReasoningResponse(
                text=text,
                model=model,
                request_id=request_id,
                usage=normalized,
                estimated_cost=self._cost_estimator(normalized),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("OpenAI returned malformed reasoning data") from error


def response_header_id(
    payload: Mapping[str, object], headers: Mapping[str, str] | None = None
) -> str:
    """Return the provider request ID from the body or response headers."""
    if headers is not None:
        for name, header_value in headers.items():
            if name.casefold() == "x-request-id" and header_value.strip():
                return header_value
    body_value = payload.get("request_id")
    if not isinstance(body_value, str) or not body_value.strip():
        raise ValueError("reasoning response request ID is missing")
    return body_value


def _response_text(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError("reasoning response output is not a single message")
    message = value[0]
    if not isinstance(message, Mapping) or message.get("type") != "message":
        raise ValueError("reasoning response output message is invalid")
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise ValueError("reasoning response content is invalid")
    item = content[0]
    if not isinstance(item, Mapping) or item.get("type") != "output_text":
        raise ValueError("reasoning response output text is invalid")
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("reasoning response text is empty")
    return text


def _adaptation_envelope_schema() -> dict[str, object]:
    """Return the strict output schema without a runtime dependency on repo files."""
    anchor: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["block_id", "start", "end"],
        "properties": {
            "block_id": {"type": "string", "minLength": 1},
            "start": {"type": "integer", "minimum": 0},
            "end": {"type": "integer", "minimum": 0},
        },
    }
    turn: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "turn_id",
            "speaker_id",
            "kind",
            "text",
            "source_anchors",
            "claim_anchors",
        ],
        "properties": {
            "turn_id": {"type": "string", "minLength": 1},
            "speaker_id": {"type": "string", "minLength": 1},
            "kind": {"enum": ["factual", "editorial"]},
            "text": {"type": "string", "minLength": 1},
            "source_anchors": {
                "type": "array",
                "items": anchor,
            },
            "claim_anchors": {
                "type": "array",
                "items": anchor,
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "source_sha256",
            "treatment_id",
            "model",
            "request_id",
            "turns",
            "usage",
            "estimated_cost",
        ],
        "properties": {
            "schema_version": {"type": "string", "enum": ["1.0"]},
            "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "treatment_id": {"type": "string", "minLength": 1},
            "model": {"type": "string", "minLength": 1},
            "request_id": {"type": "string", "minLength": 1},
            "turns": {
                "type": "array",
                "items": turn,
                "minItems": 1,
            },
            "usage": {
                "type": "object",
                "additionalProperties": {"type": "integer", "minimum": 0},
            },
            "estimated_cost": {
                "type": "string",
                "pattern": "^(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$",
            },
        },
        "$defs": {"anchor": anchor, "turn": turn},
    }


__all__ = ["OpenAIResponsesTransport", "ReasoningResponse", "ReasoningTransport"]
