"""OpenAI transcription adapter and normalized transcript values."""

import hashlib
import json
from dataclasses import dataclass

from poddown.domain import ProviderUsage
from poddown.providers.http import (
    AsyncHttpTransport,
    HttpRequest,
    ProviderSettings,
    require_success,
)

_SUPPORTED_MODELS = frozenset(
    {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"}
)


@dataclass(frozen=True)
class TranscriptWord:
    """One normalized transcript word with provider timestamps."""

    word: str
    start: float
    end: float


@dataclass(frozen=True)
class TranscriptionResult:
    """Normalized transcription output used by fidelity QA."""

    text: str
    words: tuple[TranscriptWord, ...]
    usage: ProviderUsage
    request_id: str
    model: str
    checksum: str


class OpenAITranscriber:
    """Transcribe audio with an explicitly pinned OpenAI model."""

    def __init__(
        self,
        settings: ProviderSettings,
        model: str,
        transport: AsyncHttpTransport,
    ):
        self._settings = settings
        if model not in _SUPPORTED_MODELS:
            raise ValueError(f"unsupported transcription model: {model}")
        self._model = model
        self._transport = transport

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
            text = str(payload["text"])
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
        return TranscriptionResult(
            text=text,
            words=words,
            usage=normalized_usage,
            request_id=response.headers.get("x-request-id", "unavailable"),
            model=self._model,
            checksum=hashlib.sha256(response.body).hexdigest(),
        )
