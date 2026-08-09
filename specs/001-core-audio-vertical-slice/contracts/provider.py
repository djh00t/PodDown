"""Provider-neutral contracts for the PodDown core vertical slice."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol


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
    provider: str
    model: str
    confidence: float | None
    cost: Decimal


class VoiceRenderer(Protocol):
    """Render canonical segments without owning script adaptation."""

    async def render(self, segment: RenderSegment) -> RenderCandidate:
        """Render one candidate for a rights-cleared segment."""


class Transcriber(Protocol):
    """Transcribe candidate or mastered audio for fidelity checks."""

    async def transcribe(self, audio_path: Path) -> TranscriptResult:
        """Return normalized transcript text and metering metadata."""
