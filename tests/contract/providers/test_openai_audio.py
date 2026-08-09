"""Contract tests for the OpenAI speech-rendering adapter."""

import asyncio

import pytest

from poddown.providers.http import HttpResponse, ProviderRateLimited, ProviderSettings
from poddown.providers.openai_audio import OpenAIAudioRenderer
from tests.contract.providers.helpers import mono_pcm_wave, settings


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return self.response


def test_maps_pinned_request_and_audio_metadata():
    transport = RecordingTransport(
        HttpResponse(200, {"x-request-id": "oa-request"}, mono_pcm_wave(24_000))
    )
    renderer = OpenAIAudioRenderer(
        settings=settings("secret-openai-key"),
        voice="alloy",
        model="gpt-4o-mini-tts",
        transport=transport,
    )

    result = asyncio.run(renderer.render("Exact text"))

    request = transport.requests[0]
    assert request.json == {
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "input": "Exact text",
        "response_format": "wav",
    }
    assert request.headers["Authorization"] == "Bearer secret-openai-key"
    assert result.submitted_text == "Exact text"
    assert result.request_id == "oa-request"
    assert result.provider == "openai"
    assert len(result.checksum) == 64


def test_classifies_rate_limit_without_credential_leak():
    transport = RecordingTransport(HttpResponse(429, {}, b"limited"))
    renderer = OpenAIAudioRenderer(settings("do-not-leak"), "alloy", "model", transport)

    with pytest.raises(ProviderRateLimited) as error:
        asyncio.run(renderer.render("text"))

    assert "do-not-leak" not in str(error.value)


def test_rejects_empty_audio():
    renderer = OpenAIAudioRenderer(
        settings(), "alloy", "model", RecordingTransport(HttpResponse(200, {}, b""))
    )
    with pytest.raises(ValueError, match="audio"):
        asyncio.run(renderer.render("text"))


def test_rejects_empty_or_placeholder_credentials():
    with pytest.raises(ValueError, match="credential"):
        ProviderSettings.from_environment("KEY", {"KEY": "replace-me"})
