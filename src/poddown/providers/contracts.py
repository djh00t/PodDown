"""Capability and adapter ports for audio providers."""

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from poddown.domain import CandidateResult, ProviderUsage

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class TranscriptWord:
    """One normalized transcript word with provider timestamps."""

    word: str
    start: float
    end: float

    def __post_init__(self) -> None:
        if not isinstance(self.word, str) or not self.word:
            raise ValueError("transcript word must be non-empty")
        if (
            not math.isfinite(self.start)
            or not math.isfinite(self.end)
            or self.start < 0
            or self.end < self.start
        ):
            raise ValueError("transcript word timestamps are invalid")


@dataclass(frozen=True)
class TranscriptResult:
    """Provider-neutral transcription output used by fidelity QA.

    ``checksum`` is the SHA-256 digest of the exact audio bytes supplied to
    ``Transcriber.transcribe``. The activity boundary verifies that binding
    before transcript text can influence a quality gate.
    """

    text: str
    words: tuple[TranscriptWord, ...]
    provider: str
    model: str
    usage: ProviderUsage
    request_id: str
    checksum: str
    cost: Decimal = Decimal("0")
    confidence: float | None = None
    mode: str = "provider"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("transcript text must be a string")
        if not self.text.strip():
            raise ValueError("transcript text must be non-empty")
        if not isinstance(self.words, tuple) or not all(
            isinstance(word, TranscriptWord) for word in self.words
        ):
            raise ValueError("transcript words must be a tuple of TranscriptWord")
        for name, value in (
            ("provider", self.provider),
            ("model", self.model),
            ("request_id", self.request_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be non-empty")
        if not isinstance(self.usage, ProviderUsage):
            raise ValueError("transcript usage must be ProviderUsage")
        if (
            type(self.usage.input_units) is not int
            or self.usage.input_units < 0
            or type(self.usage.output_units) is not int
            or self.usage.output_units < 0
        ):
            raise ValueError("transcript usage units must be non-negative integers")
        if (
            not isinstance(self.checksum, str)
            or _SHA256.fullmatch(self.checksum) is None
        ):
            raise ValueError("transcript checksum must be a lowercase SHA-256 digest")
        if (
            not isinstance(self.cost, Decimal)
            or not self.cost.is_finite()
            or self.cost < 0
        ):
            raise ValueError("transcript cost must be a finite non-negative Decimal")
        if not isinstance(self.mode, str) or not self.mode:
            raise ValueError("transcript mode must be non-empty")
        if self.confidence is not None and (
            not isinstance(self.confidence, float)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("transcript confidence must be between zero and one")


# The adapter originally exposed this spelling. Keep it as a compatibility alias
# while ``TranscriptResult`` remains the provider-neutral contract name.
TranscriptionResult = TranscriptResult


class Transcriber(Protocol):
    """Asynchronously transcribe verified audio bytes behind an adapter port."""

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        """Return normalized transcript text and metering metadata."""


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
