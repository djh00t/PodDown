"""Contract tests for the ElevenLabs voice-rendering adapter."""

import asyncio
from decimal import Decimal

import pytest

from poddown.providers.elevenlabs import ElevenLabsRenderer
from poddown.providers.http import (
    HttpResponse,
    ProviderRateLimited,
    ProviderSettings,
)
from tests.contract.providers.helpers import mono_pcm_wave, settings


class RecordingTransport:
    """Deterministic injected transport that records a single request."""

    def __init__(self, response: HttpResponse):
        self.response = response
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return self.response


def test_maps_explicit_voice_model_audio_and_cost():
    transport = RecordingTransport(
        HttpResponse(
            status=200,
            headers={"request-id": "el-request", "x-character-count": "11"},
            body=mono_pcm_wave(44_100),
        )
    )
    renderer = ElevenLabsRenderer(
        settings=settings("secret-eleven-key"),
        voice_id="voice-1",
        model="eleven_multilingual_v2",
        transport=transport,
        cost_per_character=Decimal("0.0001"),
    )

    result = asyncio.run(renderer.render("Hello world"))

    request = transport.requests[0]
    assert request.json == {
        "text": "Hello world",
        "model_id": "eleven_multilingual_v2",
    }
    assert request.url.endswith("/voice-1?output_format=wav_44100")
    assert request.headers["xi-api-key"] == "secret-eleven-key"
    assert "Idempotency-Key" not in request.headers
    assert result.submitted_text == "Hello world"
    assert result.model == "eleven_multilingual_v2"
    assert result.usage.input_units == 11
    assert result.cost == Decimal("0.0011")
    assert len(result.checksum) == 64


@pytest.mark.parametrize("status", [429, 503])
def test_classifies_retryable_responses_without_leaking_credentials(status):
    transport = RecordingTransport(HttpResponse(status, {}, b"provider failure"))
    renderer = ElevenLabsRenderer(
        settings=settings("do-not-leak"),
        voice_id="voice-1",
        model="model-1",
        transport=transport,
    )

    with pytest.raises(ProviderRateLimited if status == 429 else TimeoutError) as error:
        asyncio.run(renderer.render("immutable"))

    assert "do-not-leak" not in str(error.value)


def test_rejects_malformed_audio():
    transport = RecordingTransport(HttpResponse(200, {}, b""))
    renderer = ElevenLabsRenderer(settings(), "voice", "model", transport)

    with pytest.raises(ValueError, match="audio"):
        asyncio.run(renderer.render("text"))


def test_rejects_truncated_wave_data():
    valid = mono_pcm_wave(44_100)
    transport = RecordingTransport(HttpResponse(200, {}, valid[:-8]))
    renderer = ElevenLabsRenderer(settings(), "voice", "model", transport)

    with pytest.raises(ValueError, match="audio"):
        asyncio.run(renderer.render("text"))


@pytest.mark.parametrize("rate", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_rejects_invalid_pricing_before_dispatch(rate):
    transport = RecordingTransport(HttpResponse(200, {}, mono_pcm_wave(44_100)))

    with pytest.raises(ValueError, match="cost"):
        ElevenLabsRenderer(
            settings(), "voice", "model", transport, cost_per_character=rate
        )

    assert transport.requests == []


def test_rejects_empty_or_placeholder_credentials():
    for key in ("", "replace-me", "changeme"):
        with pytest.raises(ValueError, match="credential"):
            ProviderSettings.from_environment("KEY", {"KEY": key})


def test_environment_settings_are_redacted_and_enforce_budget_before_dispatch():
    settings = ProviderSettings.from_environment(
        "ELEVENLABS_API_KEY",
        {"ELEVENLABS_API_KEY": "environment-secret"},
    )
    transport = RecordingTransport(HttpResponse(200, {}, mono_pcm_wave(44_100)))
    renderer = ElevenLabsRenderer(
        settings,
        "voice",
        "model",
        transport,
        Decimal("0.001"),
        max_cost_per_request=Decimal("0.0001"),
        zero_retention=True,
    )

    assert "environment-secret" not in repr(settings)
    with pytest.raises(ValueError, match="budget"):
        asyncio.run(renderer.render("too expensive"))
    assert transport.requests == []
