"""Capability and adapter ports for audio providers."""

from dataclasses import dataclass
from typing import Protocol

from poddown.domain import CandidateResult


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities required for safe provider selection."""

    formats: frozenset[str]
    sample_rates: frozenset[int]
    max_text_characters: int
    model_pinning: bool
    voice_pinning: bool
    timestamps: bool
    provider_idempotency: bool


class VoiceRenderer(Protocol):
    """Asynchronous renderer isolated behind normalized PodDown contracts."""

    capabilities: ProviderCapabilities

    async def render(self, text: str) -> CandidateResult:
        """Render immutable expected-spoken text into one candidate."""
