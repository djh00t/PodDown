"""Provider-neutral contracts for the PodDown core vertical slice."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from poddown.domain import ProviderUsage


@dataclass(frozen=True)
class RenderSegment:
    """Immutable provider input for one canonical script segment."""

    segment_id: str
    speaker_id: str
    text: str
    expected_spoken_text: str
    voice_asset_id: str
    idempotency_key: str


@dataclass(frozen=True)
class RenderCandidate:
    """One immutable audio take returned by a voice provider."""

    candidate_id: str
    segment_id: str
    audio_path: Path
    provider: str
    model: str
    request_id: str
    cost: Decimal


@dataclass(frozen=True)
class TranscriptResult:
    """Normalized transcription output used by fidelity QA."""

    text: str
    words: tuple["TranscriptWord", ...]
    provider: str
    model: str
    usage: ProviderUsage
    request_id: str
    checksum: str  # SHA-256 of the exact audio bytes sent to the transcriber.
    cost: Decimal
    confidence: float | None = None
    mode: str = "provider"


@dataclass(frozen=True)
class TranscriptWord:
    """Optional provider word timing retained when the adapter supplies it."""

    word: str
    start: float
    end: float


class VoiceRenderer(Protocol):
    """Render canonical segments without owning script adaptation."""

    async def render(self, segment: RenderSegment) -> RenderCandidate:
        """Render one candidate for a rights-cleared segment."""


class Transcriber(Protocol):
    """Transcribe candidate or mastered audio for fidelity checks."""

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        """Return normalized transcript text and metering metadata."""
