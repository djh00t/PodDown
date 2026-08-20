"""Injected runtime construction for configured OpenAI transcribers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from poddown.domain import ProviderUsage
from poddown.provider_routes import ProviderBinding
from poddown.providers.contracts import Transcriber
from poddown.providers.http import AsyncHttpTransport, ProviderSettings
from poddown.providers.openai_transcription import OpenAITranscriber


@dataclass(frozen=True, slots=True)
class OpenAITranscriptionPricing:
    """Configured per-audio-second rates for explicitly pinned models."""

    cost_per_audio_second: Mapping[str, Decimal]
    _rates: Mapping[str, Decimal] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        rates = dict(self.cost_per_audio_second)
        if not rates:
            raise ValueError("OpenAI transcription pricing must not be empty")
        for model, rate in rates.items():
            if not isinstance(model, str) or not model.strip():
                raise ValueError("OpenAI transcription pricing model is invalid")
            if not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0:
                raise ValueError(
                    "OpenAI transcription audio-second cost must be a positive Decimal"
                )
        object.__setattr__(self, "_rates", MappingProxyType(rates))

    def ensure_model(self, model: str) -> None:
        """Require a configured rate for one pinned transcription model."""
        if model not in self._rates:
            raise ValueError(f"OpenAI transcription pricing is missing for {model}")

    def estimate(self, model: str, usage: ProviderUsage) -> Decimal:
        """Estimate cost from normalized positive audio-second usage."""
        self.ensure_model(model)
        if (
            not isinstance(usage, ProviderUsage)
            or type(usage.input_units) is not int
            or usage.input_units <= 0
        ):
            raise ValueError("OpenAI transcription audio seconds must be positive")
        if type(usage.output_units) is not int or usage.output_units < 0:
            raise ValueError("OpenAI transcription output usage must be non-negative")
        return self._rates[model] * Decimal(usage.input_units)


@dataclass(frozen=True, slots=True)
class OpenAITranscriberFactory:
    """Build OpenAI transcribers from secret-safe route configuration."""

    settings: ProviderSettings
    transport: AsyncHttpTransport
    pricing: OpenAITranscriptionPricing

    def create(self, binding: ProviderBinding) -> Transcriber:
        """Return an adapter pinned to the binding model and configured rate."""
        if not isinstance(binding, ProviderBinding):
            raise ValueError("binding must be a ProviderBinding")
        if binding.provider != "openai":
            raise ValueError("OpenAI transcriber factory requires an OpenAI binding")
        self.pricing.ensure_model(binding.model)
        return OpenAITranscriber(
            settings=self.settings,
            model=binding.model,
            transport=self.transport,
            cost_estimator=lambda _audio, usage: self.pricing.estimate(
                binding.model, usage
            ),
        )


__all__ = ["OpenAITranscriberFactory", "OpenAITranscriptionPricing"]
