"""OpenAI transcription adapter and normalized transcript values."""

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal

from poddown.domain import ProviderUsage
from poddown.providers.contracts import TranscriptResult, TranscriptWord
from poddown.providers.http import (
    AsyncHttpTransport,
    HttpRequest,
    ProviderSettings,
    require_success,
)

_SUPPORTED_MODELS = frozenset(
    {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"}
)


# Preserve the original module-level name for callers of the initial adapter.
TranscriptionResult = TranscriptResult


class OpenAITranscriber:
    """Transcribe audio with an explicitly pinned OpenAI model."""

    def __init__(
        self,
        settings: ProviderSettings,
        model: str,
        transport: AsyncHttpTransport,
        cost_estimator: Callable[[bytes, ProviderUsage], Decimal] | None = None,
    ):
        self._settings = settings
        if model not in _SUPPORTED_MODELS:
            raise ValueError(f"unsupported transcription model: {model}")
        self._model = model
        self._transport = transport
        self._cost_estimator = cost_estimator or (lambda _audio, _usage: Decimal("0"))

    async def transcribe(self, audio: bytes) -> TranscriptionResult:
        """Return normalized transcript data without inventing absent fields."""
        if not audio:
            raise ValueError("audio must not be empty")
        form = {"model": self._model, "response_format": "json"}
        if self._model == "whisper-1":
            form = {
                "model": self._model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
            }
        response = await self._transport.request(
            HttpRequest(
                method="POST",
                url="https://api.openai.com/v1/audio/transcriptions",
                headers={
                    "Authorization": (
                        f"Bearer {self._settings.api_key.get_secret_value()}"
                    )
                },
                form=form,
                files={"file": ("segment.wav", audio, "audio/wav")},
                timeout_seconds=self._settings.timeout_seconds,
            )
        )
        require_success(response, "OpenAI")
        try:
            payload = json.loads(response.body)
            text = payload["text"]
            if not isinstance(text, str):
                raise TypeError("text must be a string")
            words = tuple(
                TranscriptWord(
                    str(word["word"]), float(word["start"]), float(word["end"])
                )
                for word in payload.get("words", [])
            )
            usage = payload.get("usage", {})
            normalized_usage = ProviderUsage(
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("OpenAI returned malformed transcription data") from error
        return TranscriptResult(
            text=text,
            words=words,
            provider="openai",
            usage=normalized_usage,
            request_id=response.headers.get("x-request-id", "unavailable"),
            model=self._model,
            checksum=hashlib.sha256(audio).hexdigest(),
            cost=self._cost_estimator(audio, normalized_usage),
            confidence=(
                float(payload["confidence"])
                if payload.get("confidence") is not None
                else None
            ),
        )
