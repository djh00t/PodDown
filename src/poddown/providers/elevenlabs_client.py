"""Low-level ElevenLabs synthesis client with a safe response boundary."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from poddown.domain import ProviderUsage
from poddown.providers.http import (
    AsyncHttpTransport,
    HttpRequest,
    ProviderSettings,
    require_success,
    require_wave_audio,
)


@dataclass(frozen=True, slots=True)
class ElevenLabsSynthesisResult:
    """Validated provider response data safe to pass above the HTTP client."""

    audio_bytes: bytes
    request_id: str | None
    model: str
    voice_id: str
    usage: ProviderUsage
    estimated_cost: Decimal


class ElevenLabsClient:
    """Synthesize WAV audio through an explicitly injected HTTP transport."""

    def __init__(
        self,
        settings: ProviderSettings,
        voice_id: str,
        model: str,
        transport: AsyncHttpTransport,
        cost_per_character: Decimal = Decimal("0"),
        max_cost_per_request: Decimal = Decimal("10"),
        zero_retention: bool = False,
    ) -> None:
        if not isinstance(voice_id, str) or not voice_id.strip():
            raise ValueError("ElevenLabs voice ID must be non-empty")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("ElevenLabs model must be non-empty")
        if not isinstance(settings, ProviderSettings):
            raise TypeError("settings must be ProviderSettings")
        if not hasattr(transport, "request"):
            raise TypeError("transport must expose request(request)")
        if not cost_per_character.is_finite() or cost_per_character < 0:
            raise ValueError("ElevenLabs character cost must be finite and nonnegative")
        if not max_cost_per_request.is_finite() or max_cost_per_request < 0:
            raise ValueError("ElevenLabs request budget must be finite and nonnegative")
        if type(zero_retention) is not bool:
            raise TypeError("zero_retention must be a boolean")
        self._settings = settings
        self._voice_id = voice_id
        self._model = model
        self._transport = transport
        self._cost_per_character = cost_per_character
        self._max_cost_per_request = max_cost_per_request
        self._zero_retention = zero_retention

    async def synthesize(self, text: str) -> ElevenLabsSynthesisResult:
        """Return validated provider audio and metadata without exposing credentials."""
        if not isinstance(text, str) or not text:
            raise ValueError("ElevenLabs text must be non-empty")
        estimated_cost = self._cost_per_character * len(text)
        if estimated_cost > self._max_cost_per_request:
            raise ValueError("ElevenLabs request exceeds configured budget")
        retention = "&enable_logging=false" if self._zero_retention else ""
        response = await self._transport.request(
            HttpRequest(
                method="POST",
                url=(
                    "https://api.elevenlabs.io/v1/text-to-speech/"
                    f"{self._voice_id}?output_format=wav_44100{retention}"
                ),
                headers={
                    "xi-api-key": self._settings.api_key.get_secret_value(),
                    "Content-Type": "application/json",
                },
                json={"text": text, "model_id": self._model},
                timeout_seconds=self._settings.timeout_seconds,
            )
        )
        require_success(response, "ElevenLabs")
        require_wave_audio(response.body, "ElevenLabs", 44_100)
        raw_units = response.headers.get("x-character-count")
        units = len(text) if raw_units is None else int(raw_units)
        if units < 0:
            raise ValueError("ElevenLabs returned invalid character usage")
        return ElevenLabsSynthesisResult(
            audio_bytes=response.body,
            request_id=response.headers.get("request-id"),
            model=self._model,
            voice_id=self._voice_id,
            usage=ProviderUsage(units, len(response.body)),
            estimated_cost=self._cost_per_character * units,
        )


__all__ = ["ElevenLabsClient", "ElevenLabsSynthesisResult"]
