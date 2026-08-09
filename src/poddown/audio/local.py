"""Deterministic stdlib-only local WAV renderer for durable-audio tests."""

import hashlib
import wave
from decimal import Decimal
from io import BytesIO

from poddown.audio.contracts import RenderedAudio, RenderRequest
from poddown.domain import ProviderUsage
from poddown.providers.contracts import ProviderCapabilities


class DeterministicLocalRenderer:
    """Render request-bound deterministic PCM WAV bytes without external services."""

    capabilities = ProviderCapabilities(
        formats=frozenset({"wav"}),
        sample_rates=frozenset({44_100}),
        max_text_characters=100_000,
        model_pinning=True,
        voice_pinning=True,
        timestamps=False,
        provider_idempotency=True,
    )

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def render(self, request: RenderRequest) -> RenderedAudio:
        """Render one request into a deterministic mono 16-bit PCM WAV file."""
        self.calls.append(request.idempotency_key)
        digest = hashlib.sha256(request.idempotency_key.encode("ascii")).digest()
        frame_count = 1_024 + int.from_bytes(digest[:2], "big") % 1_024
        frames = b"".join(
            ((digest[index % len(digest)] - 128) * 255).to_bytes(
                2, "little", signed=True
            )
            for index in range(frame_count)
        )
        buffer = BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(request.sample_rate_hz)
            output.writeframes(frames)
        audio_bytes = buffer.getvalue()
        return RenderedAudio(
            audio_bytes=audio_bytes,
            provider=request.provider,
            model=request.model,
            request_id=f"local-{request.idempotency_key[7:19]}",
            usage=ProviderUsage(
                input_units=len(request.expected_spoken_text),
                output_units=len(audio_bytes),
            ),
            cost=Decimal("0"),
            output_format=request.output_format,
            sample_rate_hz=request.sample_rate_hz,
        )
