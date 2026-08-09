"""Contract tests for the OpenAI transcription adapter."""

import asyncio
from decimal import Decimal

import pytest

from poddown.domain import ProviderUsage
from poddown.providers.http import HttpResponse, ProviderSettings
from poddown.providers.contracts import (
    TranscriptWord,
    Transcriber,
    TranscriptionResult,
)
from poddown.providers.openai_transcription import OpenAITranscriber
from tests.contract.providers.helpers import settings


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return self.response


def test_provider_neutral_transcriber_accepts_audio_bytes_and_normalized_result():
    class ByteTranscriber:
        async def transcribe(self, audio: bytes) -> TranscriptionResult:
            assert audio == b"RIFF-audio"
            return TranscriptionResult(
                provider="openai",
                text="hello",
                words=(TranscriptWord("hello", 0.0, 0.2),),
                usage=ProviderUsage(7, 5),
                request_id="tx-request",
                checksum="a" * 64,
                cost=Decimal("0.0012"),
            )

    transcriber: Transcriber = ByteTranscriber()
    result = asyncio.run(transcriber.transcribe(b"RIFF-audio"))

    assert result.provider == "openai"
    assert result.text == "hello"
    assert result.words == (TranscriptWord("hello", 0.0, 0.2),)
    assert result.usage == ProviderUsage(7, 5)
    assert result.request_id == "tx-request"
    assert result.checksum == "a" * 64
    assert result.cost == Decimal("0.0012")


def test_normalizes_text_words_usage_and_checksum():
    body = (
        b'{"text":"one point six terabit","words":[{"word":"one",'
        b'"start":0.0,"end":0.2}],"usage":{"input_tokens":7,'
        b'"output_tokens":5}}'
    )
    transport = RecordingTransport(
        HttpResponse(200, {"x-request-id": "tx-request"}, body)
    )
    transcriber = OpenAITranscriber(
        settings=settings("secret-openai-key"),
        model="whisper-1",
        transport=transport,
    )

    result = asyncio.run(transcriber.transcribe(b"RIFF-audio"))

    request = transport.requests[0]
    assert request.form["model"] == "whisper-1"
    assert request.form["response_format"] == "verbose_json"
    assert request.files["file"] == ("segment.wav", b"RIFF-audio", "audio/wav")
    assert result.text == "one point six terabit"
    assert result.words[0].word == "one"
    assert result.usage.input_units == 7
    assert result.usage.output_units == 5
    assert result.request_id == "tx-request"
    assert len(result.checksum) == 64


def test_gpt_transcription_uses_json_without_timestamps():
    transport = RecordingTransport(HttpResponse(200, {}, b'{"text":"hello"}'))
    transcriber = OpenAITranscriber(settings(), "gpt-4o-transcribe", transport)

    result = asyncio.run(transcriber.transcribe(b"RIFF-audio"))

    assert transport.requests[0].form == {
        "model": "gpt-4o-transcribe",
        "response_format": "json",
    }
    assert result.words == ()


def test_rejects_malformed_json_without_leaking_credentials():
    transport = RecordingTransport(HttpResponse(200, {}, b"not-json"))
    transcriber = OpenAITranscriber(settings("do-not-leak"), "whisper-1", transport)

    with pytest.raises(ValueError) as error:
        asyncio.run(transcriber.transcribe(b"RIFF-audio"))

    assert "do-not-leak" not in str(error.value)


def test_rejects_empty_input_before_dispatch():
    transport = RecordingTransport(HttpResponse(200, {}, b"{}"))
    transcriber = OpenAITranscriber(settings(), "whisper-1", transport)

    with pytest.raises(ValueError, match="audio"):
        asyncio.run(transcriber.transcribe(b""))

    assert transport.requests == []


def test_rejects_unsupported_model_and_placeholder_credentials():
    with pytest.raises(ValueError, match="model"):
        OpenAITranscriber(settings(), "unknown", RecordingTransport(None))
    with pytest.raises(ValueError, match="credential"):
        ProviderSettings.from_environment("KEY", {"KEY": "changeme"})
