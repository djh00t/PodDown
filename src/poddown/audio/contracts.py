"""Immutable contracts for deterministic durable-audio rendering."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from poddown.domain import ProviderUsage
from poddown.providers.contracts import ProviderCapabilities

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OUTPUT_FORMATS = frozenset({"wav"})


def _non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _positive_integer(name: str, value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _non_negative_integer(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _usage(value: object) -> ProviderUsage:
    if not isinstance(value, ProviderUsage):
        raise ValueError("usage must be ProviderUsage")
    _positive_integer("usage input_units", value.input_units)
    _positive_integer("usage output_units", value.output_units)
    return value


def _cost(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("cost must be a finite non-negative Decimal")
    return value


@dataclass(frozen=True)
class ArtifactRef:
    """Immutable reference to a content-addressed audio artifact."""

    sha256: str
    media_type: str
    size_bytes: int
    relative_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("sha256 must be a lowercase SHA-256 hex digest")
        _non_empty_string("media_type", self.media_type)
        _non_negative_integer("size_bytes", self.size_bytes)
        _non_empty_string("relative_path", self.relative_path)


@dataclass(frozen=True)
class RenderRequest:
    """A complete immutable provider request for one deterministic audio take."""

    episode_id: str
    episode_version: str
    segment_id: str
    speaker_id: str
    expected_spoken_text: str
    voice_asset_id: str
    provider: str
    model: str
    attempt: int = 1
    take_index: int = 0
    output_format: str = "wav"
    sample_rate_hz: int = 44_100

    def __post_init__(self) -> None:
        for name in (
            "episode_id",
            "episode_version",
            "segment_id",
            "speaker_id",
            "expected_spoken_text",
            "voice_asset_id",
            "provider",
            "model",
        ):
            _non_empty_string(name, getattr(self, name))
        _positive_integer("attempt", self.attempt)
        _non_negative_integer("take_index", self.take_index)
        if self.output_format not in _OUTPUT_FORMATS:
            raise ValueError("output_format must be wav")
        _positive_integer("sample_rate_hz", self.sample_rate_hz)

    def _identity_digest(self) -> str:
        payload = {
            "attempt": self.attempt,
            "episode_id": self.episode_id,
            "episode_version": self.episode_version,
            "expected_spoken_text": self.expected_spoken_text,
            "model": self.model,
            "output_format": self.output_format,
            "provider": self.provider,
            "sample_rate_hz": self.sample_rate_hz,
            "segment_id": self.segment_id,
            "speaker_id": self.speaker_id,
            "take_index": self.take_index,
            "voice_asset_id": self.voice_asset_id,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def idempotency_key(self) -> str:
        """Return the content-derived key used for replay-safe persistence."""
        return f"render-{self._identity_digest()}"

    @property
    def candidate_id(self) -> str:
        """Return the content-derived identity for this exact render take."""
        return f"candidate-{self._identity_digest()}"


@dataclass(frozen=True)
class RenderedAudio:
    """Normalized bytes and provider metadata returned from a renderer."""

    audio_bytes: bytes
    provider: str
    model: str
    request_id: str
    usage: ProviderUsage
    cost: Decimal
    output_format: str
    sample_rate_hz: int

    def __post_init__(self) -> None:
        if not isinstance(self.audio_bytes, bytes) or not self.audio_bytes:
            raise ValueError("audio_bytes must be non-empty bytes")
        _non_empty_string("provider", self.provider)
        _non_empty_string("model", self.model)
        _non_empty_string("request_id", self.request_id)
        _usage(self.usage)
        _cost(self.cost)
        if self.output_format not in _OUTPUT_FORMATS:
            raise ValueError("output_format must be wav")
        _positive_integer("sample_rate_hz", self.sample_rate_hz)


@dataclass(frozen=True)
class ProviderCostEvent:
    """Immutable metering evidence for one rendered candidate."""

    event_id: str
    candidate_id: str
    provider: str
    usage: ProviderUsage
    cost: Decimal

    def __post_init__(self) -> None:
        _non_empty_string("event_id", self.event_id)
        _non_empty_string("candidate_id", self.candidate_id)
        _non_empty_string("provider", self.provider)
        _usage(self.usage)
        _cost(self.cost)


@dataclass(frozen=True)
class RenderCandidate:
    """Persistable immutable result of one accepted audio render."""

    candidate_id: str
    idempotency_key: str
    segment_id: str
    speaker_id: str
    attempt: int
    take_index: int
    voice_asset_id: str
    expected_spoken_text: str
    provider: str
    model: str
    request_id: str
    usage: ProviderUsage
    cost: Decimal
    artifact: ArtifactRef

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "idempotency_key",
            "segment_id",
            "speaker_id",
            "voice_asset_id",
            "expected_spoken_text",
            "provider",
            "model",
            "request_id",
        ):
            _non_empty_string(name, getattr(self, name))
        _positive_integer("attempt", self.attempt)
        _non_negative_integer("take_index", self.take_index)
        _usage(self.usage)
        _cost(self.cost)
        if not isinstance(self.artifact, ArtifactRef):
            raise ValueError("artifact must be ArtifactRef")


@dataclass(frozen=True)
class RenderOutcome:
    """A durable candidate result, optionally with newly recorded cost evidence."""

    candidate: RenderCandidate
    cost_event: ProviderCostEvent | None
    replayed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, RenderCandidate):
            raise ValueError("candidate must be RenderCandidate")
        if self.cost_event is not None and not isinstance(
            self.cost_event, ProviderCostEvent
        ):
            raise ValueError("cost_event must be ProviderCostEvent or None")
        if type(self.replayed) is not bool:
            raise ValueError("replayed must be a boolean")


class AudioRenderer(Protocol):
    """Provider-neutral renderer port used by the durable render service."""

    capabilities: ProviderCapabilities

    def render(self, request: RenderRequest) -> RenderedAudio:
        """Render one immutable request into normalized audio bytes."""
