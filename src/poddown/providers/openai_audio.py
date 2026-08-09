"""OpenAI implementation of PodDown's voice-renderer port."""

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


class OpenAIAudioRenderer:
    """Render one immutable segment with a pinned OpenAI model and voice."""

    capabilities = ProviderCapabilities(
        formats=frozenset({"wav"}),
        sample_rates=frozenset({24_000}),
        max_text_characters=4_096,
        model_pinning=True,
        voice_pinning=True,
        timestamps=False,
        provider_idempotency=False,
    )

    def __init__(
        self,
        settings: ProviderSettings,
        voice: str,
        model: str,
        transport: AsyncHttpTransport,
    ):
        self._settings = settings
        self._voice = voice
        self._model = model
        self._transport = transport

    async def render(self, text: str) -> CandidateResult:
        """Render one candidate without allowing provider-side adaptation."""
        candidate_id = str(uuid4())
        response = await self._transport.request(
            HttpRequest(
                method="POST",
                url="https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": (
                        f"Bearer {self._settings.api_key.get_secret_value()}"
                    ),
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "voice": self._voice,
                    "input": text,
                    "response_format": "wav",
                },
                timeout_seconds=self._settings.timeout_seconds,
            )
        )
        require_success(response, "OpenAI")
        require_wave_audio(response.body, "OpenAI", 24_000)
        return CandidateResult(
            accepted=True,
            provider_calls=1,
            provider="openai",
            candidate_id=candidate_id,
            submitted_text=text,
            request_id=response.headers.get("x-request-id", candidate_id),
            model=self._model,
            usage=ProviderUsage(len(text), len(response.body)),
            cost=Decimal("0"),
            checksum=hashlib.sha256(response.body).hexdigest(),
        )
