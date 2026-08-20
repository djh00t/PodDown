"""Unit contracts for the injected OpenAI transcription factory."""

import asyncio
from decimal import Decimal

import pytest
from pydantic import SecretStr

from poddown.domain import ProviderUsage
from poddown.provider_routes import ProviderBinding
from poddown.providers.http import HttpResponse, ProviderSettings
from poddown.providers.provider_factory import (
    OpenAITranscriberFactory,
    OpenAITranscriptionPricing,
)


class RecordingTransport:
    """Deterministic provider fake that never makes a network call."""

    def __init__(self, response: HttpResponse):
        self.response = response
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return self.response


def _binding(*, provider: str = "openai", model: str = "whisper-1") -> ProviderBinding:
    return ProviderBinding(
        provider=provider,
        model=model,
        required_capabilities=frozenset({"timestamps"}),
        secret_ref="env://OPENAI_API_KEY" if provider == "openai" else None,
    )


def _settings() -> ProviderSettings:
    return ProviderSettings(api_key=SecretStr("test-only-secret"))


def _pricing(*, rate: Decimal = Decimal("0.0001")) -> OpenAITranscriptionPricing:
    return OpenAITranscriptionPricing(cost_per_audio_second={"whisper-1": rate})


def test_factory_pins_model_and_prices_normalized_audio_seconds() -> None:
    """The selected binding model and Decimal cost must reach the result."""
    transport = RecordingTransport(
        HttpResponse(
            200,
            {"x-request-id": "transcribe-1"},
            b'{"text":"hello","words":[{"word":"hello",'
            b'"start":0.0,"end":0.2}],"usage":{"type":"duration",'
            b'"seconds":30.0}}',
        )
    )
    transcriber = OpenAITranscriberFactory(
        settings=_settings(), transport=transport, pricing=_pricing()
    ).create(_binding())

    result = asyncio.run(transcriber.transcribe(b"RIFF-audio"))

    assert transport.requests[0].form["model"] == "whisper-1"
    assert result.usage == ProviderUsage(input_units=30, output_units=0)
    assert result.cost == Decimal("0.0030")


def test_factory_rejects_missing_model_pricing_before_dispatch() -> None:
    """A route without a configured price cannot construct a live transcriber."""
    factory = OpenAITranscriberFactory(
        settings=_settings(),
        transport=RecordingTransport(HttpResponse(200, {}, b"{}")),
        pricing=OpenAITranscriptionPricing(
            cost_per_audio_second={"other-model": Decimal("0.0001")}
        ),
    )

    with pytest.raises(ValueError, match="pricing"):
        factory.create(_binding())


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-0.0001"), Decimal("NaN")])
def test_pricing_rejects_missing_or_invalid_rates(rate: Decimal) -> None:
    """Configured provider rates must be finite and strictly positive."""
    with pytest.raises(ValueError, match="audio-second"):
        OpenAITranscriptionPricing(cost_per_audio_second={"whisper-1": rate})


@pytest.mark.parametrize("usage", [ProviderUsage(0, 0), ProviderUsage(-1, 0)])
def test_pricing_rejects_zero_or_negative_audio_seconds(
    usage: ProviderUsage,
) -> None:
    """A provider response without positive billable usage fails closed."""
    with pytest.raises(ValueError, match="audio seconds"):
        _pricing().estimate("whisper-1", usage)


def test_factory_rejects_non_openai_binding() -> None:
    """The OpenAI factory cannot be used as a silent provider fallback."""
    factory = OpenAITranscriberFactory(
        settings=_settings(),
        transport=RecordingTransport(HttpResponse(200, {}, b"{}")),
        pricing=_pricing(),
    )

    with pytest.raises(ValueError, match="OpenAI"):
        factory.create(_binding(provider="host-local"))


def test_factory_repr_does_not_expose_the_provider_secret() -> None:
    """Factory diagnostics use Pydantic redaction and route references only."""
    factory = OpenAITranscriberFactory(
        settings=ProviderSettings(api_key=SecretStr("raw-provider-secret")),
        transport=RecordingTransport(HttpResponse(200, {}, b"{}")),
        pricing=_pricing(),
    )

    assert "raw-provider-secret" not in repr(factory)
