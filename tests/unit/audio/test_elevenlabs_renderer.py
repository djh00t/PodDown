"""Unit tests for the normalized ElevenLabs audio renderer."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from poddown.audio.contracts import RenderRequest
from poddown.audio.elevenlabs import ElevenLabsAudioRenderer
from poddown.domain import ProviderUsage
from poddown.providers.elevenlabs_client import ElevenLabsSynthesisResult
from tests.contract.providers.helpers import mono_pcm_wave


class _Client:
    def __init__(self, result: ElevenLabsSynthesisResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> ElevenLabsSynthesisResult:
        self.calls.append(text)
        return self.result


def _request() -> RenderRequest:
    return RenderRequest(
        "episode-1",
        "v1",
        "segment-1",
        "host",
        "Immutable text.",
        "voice-1",
        "elevenlabs",
        "eleven-multilingual-v2",
    )


def _result(**overrides: object) -> ElevenLabsSynthesisResult:
    audio = mono_pcm_wave(44_100)
    values: dict[str, object] = {
        "audio_bytes": audio,
        "request_id": "request-1",
        "model": "eleven-multilingual-v2",
        "voice_id": "voice-1",
        "usage": ProviderUsage(len(_request().expected_spoken_text), len(audio)),
        "estimated_cost": Decimal("0.001"),
    }
    values.update(overrides)
    return ElevenLabsSynthesisResult(**values)  # type: ignore[arg-type]


def test_renderer_maps_verified_client_result() -> None:
    client = _Client(_result())

    rendered = asyncio.run(ElevenLabsAudioRenderer(client).render(_request()))

    assert rendered.audio_bytes == client.result.audio_bytes
    assert client.calls == [_request().expected_spoken_text]
    assert rendered.output_format == "wav"
    assert rendered.sample_rate_hz == 44_100


@pytest.mark.parametrize("field", ["model", "voice_id"])
def test_renderer_rejects_provider_identity_mismatch(field: str) -> None:
    value = "wrong-model" if field == "model" else "wrong-voice"
    client = _Client(_result(**{field: value}))

    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(ElevenLabsAudioRenderer(client).render(_request()))
