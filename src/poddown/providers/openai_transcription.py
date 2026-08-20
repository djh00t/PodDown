"""OpenAI transcription adapter and normalized transcript values."""

import hashlib
import json
import math
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
_WORD_TIMESTAMP_MODEL = "whisper-1"


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
        form = {"model": self._model}
        if self._model == _WORD_TIMESTAMP_MODEL:
            form.update(
                {
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                }
            )
        else:
            form["response_format"] = "json"
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
            words = (
                tuple(
                    TranscriptWord(
                        str(word["word"]), float(word["start"]), float(word["end"])
                    )
                    for word in payload.get("words", [])
                )
                if self._model == _WORD_TIMESTAMP_MODEL
                else ()
            )
            usage = payload.get("usage", {})
            if not isinstance(usage, dict):
                raise TypeError("usage must be an object")
            if "seconds" in usage:
                seconds = usage["seconds"]
                if (
                    isinstance(seconds, bool)
                    or not isinstance(seconds, (int, float))
                    or not math.isfinite(seconds)
                    or seconds < 0
                    or not float(seconds).is_integer()
                ):
                    raise TypeError("usage seconds must be a non-negative integer")
                input_units = int(seconds)
            else:
                input_units = usage.get("input_tokens", 0)
                if type(input_units) is not int or input_units < 0:
                    raise TypeError("input_tokens must be a non-negative integer")
            output_units = usage.get("output_tokens", 0)
            if type(output_units) is not int or output_units < 0:
                raise TypeError("output_tokens must be a non-negative integer")
            normalized_usage = ProviderUsage(input_units, output_units)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("OpenAI returned malformed transcription data") from error
        if self._model == _WORD_TIMESTAMP_MODEL and not words:
            raise ValueError("OpenAI returned no word timestamps")
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
