"""Contract tests for the normalized ElevenLabs synthesis client."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from poddown.providers.elevenlabs_client import ElevenLabsClient
from poddown.providers.http import HttpResponse
from tests.contract.providers.helpers import mono_pcm_wave, settings


class _Transport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return self.response


def test_client_preserves_voice_model_and_metering_without_idempotency_header() -> None:
    transport = _Transport(
        HttpResponse(
            200,
            {"request-id": "el-1", "x-character-count": "12"},
            mono_pcm_wave(44_100),
        )
    )
    client = ElevenLabsClient(
        settings(),
        "voice-1",
        "eleven-multilingual-v2",
        transport,
        cost_per_character=Decimal("0.0001"),
        zero_retention=True,
    )

    result = asyncio.run(client.synthesize("Hello world!"))

    request = transport.requests[0]
    assert request.json == {
        "text": "Hello world!",
        "model_id": "eleven-multilingual-v2",
    }
    assert request.url.endswith("voice-1?output_format=wav_44100&enable_logging=false")
    assert "Idempotency-Key" not in request.headers
    assert result.request_id == "el-1"
    assert result.usage.input_units == 12
    assert result.estimated_cost == Decimal("0.0012")


def test_client_rejects_invalid_usage_header_before_normalization() -> None:
    transport = _Transport(
        HttpResponse(
            200, {"x-character-count": "not-an-integer"}, mono_pcm_wave(44_100)
        )
    )
    client = ElevenLabsClient(settings(), "voice-1", "model", transport)

    with pytest.raises(ValueError, match="invalid literal"):
        asyncio.run(client.synthesize("text"))
