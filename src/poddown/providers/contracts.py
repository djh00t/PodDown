"""Capability and adapter ports for audio providers."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol

from poddown.domain import CandidateResult, ProviderUsage
from poddown.evidence import EvidenceKind

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_TEXT = re.compile(r"^\S[\s\S]{0,254}$")
_PROVIDER_EVIDENCE_OPERATIONS = frozenset({"adapt", "render", "transcribe", "publish"})


def provider_usage_to_mapping(usage: ProviderUsage) -> dict[str, int]:
    """Convert established provider usage to frozen evidence metering names."""
    if not isinstance(usage, ProviderUsage):
        raise ValueError("usage must be ProviderUsage")
    return {"input_units": usage.input_units, "output_units": usage.output_units}


@dataclass(frozen=True)
class ProviderEvidence:
    """Normalized immutable metadata for one provider operation."""

    operation: str
    provider: str
    request_id: str
    model: str
    input_sha256: str
    output_sha256: str
    usage: Mapping[str, int]
    currency: str
    estimated_cost: Decimal
    reconciled_cost: Decimal | None
    latency_ms: int
    retry_count: int
    occurred_at: datetime
    evidence_kind: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation, str)
            or self.operation not in _PROVIDER_EVIDENCE_OPERATIONS
        ):
            raise ValueError("operation must be a supported provider operation")
        if self.evidence_kind not in EvidenceKind:
            raise ValueError("evidence_kind must be a supported evidence kind")
        for name, value in (
            ("provider", self.provider),
            ("request_id", self.request_id),
            ("model", self.model),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if (
            not isinstance(self.currency, str)
            or _CURRENCY.fullmatch(self.currency) is None
        ):
            raise ValueError("currency must be an uppercase ISO-4217 code")
        for name, value in (
            ("input_sha256", self.input_sha256),
            ("output_sha256", self.output_sha256),
        ):
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not isinstance(self.usage, Mapping) or not self.usage:
            raise ValueError("usage must be a non-empty Mapping[str, int]")
        normalized_usage: dict[str, int] = {}
        for usage_key, usage_value in self.usage.items():
            if (
                not isinstance(usage_key, str)
                or _TEXT.fullmatch(usage_key) is None
                or type(usage_value) is not int
                or usage_value < 0
            ):
                raise ValueError(
                    "usage must contain non-empty keys and non-negative integers"
                )
            normalized_usage[usage_key] = usage_value
        object.__setattr__(self, "usage", MappingProxyType(normalized_usage))
        for cost_name, cost_value in (
            ("estimated_cost", self.estimated_cost),
            ("reconciled_cost", self.reconciled_cost),
        ):
            if cost_value is not None and (
                not isinstance(cost_value, Decimal)
                or not cost_value.is_finite()
                or cost_value < 0
            ):
                raise ValueError(f"{cost_name} must be a finite non-negative Decimal")
        for counter_name, counter_value in (
            ("latency_ms", self.latency_ms),
            ("retry_count", self.retry_count),
        ):
            if type(counter_value) is not int or counter_value < 0:
                raise ValueError(f"{counter_name} must be a non-negative integer")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("occurred_at must be a UTC datetime")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))


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
