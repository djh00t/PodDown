"""ElevenLabs implementation of PodDown's voice-renderer port."""

import hashlib
from decimal import Decimal
from uuid import uuid4

from poddown.domain import CandidateResult, ProviderUsage
from poddown.providers.contracts import ProviderCapabilities
from poddown.providers.http import (
    AsyncHttpTransport,
    HttpRequest,
    ProviderSettings,
    require_success,
    require_wave_audio,
)


class ElevenLabsRenderer:
    """Render immutable script segments using an authorized ElevenLabs voice."""

    capabilities = ProviderCapabilities(
        formats=frozenset({"wav"}),
        sample_rates=frozenset({44_100}),
        max_text_characters=10_000,
        model_pinning=True,
        voice_pinning=True,
        timestamps=False,
        provider_idempotency=False,
    )

    def __init__(
        self,
        settings: ProviderSettings,
        voice_id: str,
        model: str,
        transport: AsyncHttpTransport,
        cost_per_character: Decimal = Decimal("0"),
        max_cost_per_request: Decimal = Decimal("10"),
        zero_retention: bool = False,
    ):
        if not cost_per_character.is_finite() or cost_per_character < 0:
            raise ValueError("ElevenLabs character cost must be finite and nonnegative")
        if not max_cost_per_request.is_finite() or max_cost_per_request < 0:
            raise ValueError("ElevenLabs request budget must be finite and nonnegative")
        self._settings = settings
        self._voice_id = voice_id
        self._model = model
        self._transport = transport
        self._cost_per_character = cost_per_character
        self._max_cost_per_request = max_cost_per_request
        self._zero_retention = zero_retention

    async def render(self, text: str) -> CandidateResult:
        """Render one candidate without rewriting the submitted text."""
        candidate_id = str(uuid4())
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
                json={
                    "text": text,
                    "model_id": self._model,
                },
                timeout_seconds=self._settings.timeout_seconds,
            )
        )
        require_success(response, "ElevenLabs")
        require_wave_audio(response.body, "ElevenLabs", 44_100)
        units = int(response.headers.get("x-character-count", len(text)))
        return CandidateResult(
            accepted=True,
            provider_calls=1,
            provider="elevenlabs",
            candidate_id=candidate_id,
            submitted_text=text,
            request_id=response.headers.get("request-id", candidate_id),
            model=self._model,
            usage=ProviderUsage(units, len(response.body)),
            cost=self._cost_per_character * units,
            checksum=hashlib.sha256(response.body).hexdigest(),
        )
