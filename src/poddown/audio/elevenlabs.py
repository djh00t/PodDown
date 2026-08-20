"""ElevenLabs adapter for the immutable durable-audio renderer port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from poddown.audio.contracts import RenderedAudio, RenderRequest
from poddown.providers.contracts import ProviderCapabilities
from poddown.providers.elevenlabs_client import ElevenLabsSynthesisResult
from poddown.providers.http import require_wave_audio


class ElevenLabsSynthesisClient(Protocol):
    """Testable ElevenLabs client boundary needed by the audio renderer."""

    async def synthesize(self, text: str) -> ElevenLabsSynthesisResult:
        """Return validated synthesis bytes and provider metadata."""


class ElevenLabsAudioRenderer:
    """Map one pinned ElevenLabs synthesis response into durable audio."""

    capabilities = ProviderCapabilities(
        formats=frozenset({"wav"}),
        sample_rates=frozenset({44_100}),
        max_text_characters=10_000,
        model_pinning=True,
        voice_pinning=True,
        timestamps=False,
        provider_idempotency=False,
    )

    def __init__(self, client: ElevenLabsSynthesisClient) -> None:
        if not hasattr(client, "synthesize"):
            raise TypeError("client must expose synthesize(text)")
        self._client = client

    async def render(self, request: RenderRequest) -> RenderedAudio:
        """Render request text and retain only verified provider output metadata."""
        if request.provider != "elevenlabs":
            raise ValueError("ElevenLabs renderer requires an elevenlabs request")
        synthesis = await self._client.synthesize(request.expected_spoken_text)
        require_wave_audio(synthesis.audio_bytes, "ElevenLabs", request.sample_rate_hz)
        if synthesis.model != request.model:
            raise ValueError("ElevenLabs response model does not match request")
        if synthesis.voice_id != request.voice_asset_id:
            raise ValueError("ElevenLabs response voice does not match request")
        request_id = synthesis.request_id or request.candidate_id
        return RenderedAudio(
            audio_bytes=synthesis.audio_bytes,
            provider="elevenlabs",
            model=synthesis.model,
            request_id=request_id,
            usage=synthesis.usage,
            cost=synthesis.estimated_cost,
            output_format=request.output_format,
            sample_rate_hz=request.sample_rate_hz,
        )


class RoutedElevenLabsAudioRenderer:
    """Select one pinned ElevenLabs voice without changing the request text."""

    capabilities = ElevenLabsAudioRenderer.capabilities

    def __init__(self, renderers: Mapping[str, ElevenLabsAudioRenderer]) -> None:
        if not renderers or any(
            not isinstance(voice_id, str)
            or not voice_id.strip()
            or not isinstance(renderer, ElevenLabsAudioRenderer)
            for voice_id, renderer in renderers.items()
        ):
            raise ValueError("routed ElevenLabs renderers are invalid")
        self._renderers = dict(renderers)

    async def render(self, request: RenderRequest) -> RenderedAudio:
        """Render through the exact provider voice named by the request."""
        try:
            renderer = self._renderers[request.voice_asset_id]
        except KeyError as error:
            raise ValueError("live provider voice is not mapped") from error
        return await renderer.render(request)


__all__ = [
    "ElevenLabsAudioRenderer",
    "ElevenLabsSynthesisClient",
    "RoutedElevenLabsAudioRenderer",
]
