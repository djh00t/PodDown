"""Hard-gated, deterministic ranking for rendered audio candidates."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from poddown.audio.diagnostics import AudioDiagnostics
from poddown.domain import FidelityResult, ProviderUsage
from poddown.providers.contracts import TranscriptResult, TranscriptWord


class CandidateSelectionError(ValueError):
    """Raised when a candidate cannot participate in deterministic selection."""


@dataclass(frozen=True)
class CandidateQuality:
    """Immutable quality evidence used to select one rendered candidate."""

    candidate_id: str
    fidelity: FidelityResult
    diagnostics: AudioDiagnostics
    pronunciation_passed: bool
    soft_score: Decimal
    transcription: TranscriptResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise CandidateSelectionError("candidate_id must be a non-empty string")
        if not isinstance(self.fidelity, FidelityResult):
            raise CandidateSelectionError("fidelity must be FidelityResult")
        if not isinstance(self.diagnostics, AudioDiagnostics):
            raise CandidateSelectionError("diagnostics must be AudioDiagnostics")
        if type(self.pronunciation_passed) is not bool:
            raise CandidateSelectionError("pronunciation_passed must be a boolean")
        if not isinstance(self.soft_score, Decimal) or not self.soft_score.is_finite():
            raise CandidateSelectionError("soft_score must be a finite Decimal")
        if self.transcription is not None and not isinstance(
            self.transcription, TranscriptResult
        ):
            raise CandidateSelectionError(
                "transcription must be TranscriptResult or None"
            )

    @property
    def passes_hard_gates(self) -> bool:
        """Return whether required fidelity and pronunciation gates both passed."""
        return (
            self.fidelity.passed
            and self.pronunciation_passed
            and self.diagnostics.passes_hard_gates
        )

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible quality evidence for Temporal activities."""
        payload: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "diagnostics": self.diagnostics.to_dict(),
            "fidelity": {
                "accuracy": self.fidelity.accuracy,
                "passed": self.fidelity.passed,
                "rerender_scope": self.fidelity.rerender_scope,
            },
            "pronunciation_passed": self.pronunciation_passed,
            "soft_score": str(self.soft_score),
        }
        if self.transcription is not None:
            payload["transcription"] = _serialize_transcription(self.transcription)
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CandidateQuality":
        """Reconstruct quality evidence returned by a Temporal activity."""
        if not isinstance(value, dict):
            raise CandidateSelectionError("quality evidence is malformed")
        fidelity = value.get("fidelity")
        diagnostics = value.get("diagnostics")
        transcription = (
            _deserialize_transcription(value["transcription"])
            if "transcription" in value
            else None
        )
        if not isinstance(fidelity, dict) or not isinstance(diagnostics, dict):
            raise CandidateSelectionError("quality evidence is malformed")
        try:
            return cls(
                candidate_id=value["candidate_id"],
                fidelity=FidelityResult(
                    passed=fidelity["passed"],
                    accuracy=fidelity["accuracy"],
                    rerender_scope=fidelity["rerender_scope"],
                ),
                diagnostics=AudioDiagnostics(
                    sample_rate_hz=diagnostics["sample_rate_hz"],
                    channels=diagnostics["channels"],
                    duration_seconds=diagnostics["duration_seconds"],
                    peak_amplitude=diagnostics["peak_amplitude"],
                    clipping_ratio=diagnostics["clipping_ratio"],
                    silence_ratio=diagnostics["silence_ratio"],
                ),
                pronunciation_passed=value["pronunciation_passed"],
                soft_score=Decimal(str(value["soft_score"])),
                transcription=transcription,
            )
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise CandidateSelectionError("quality evidence is malformed") from error


def _serialize_transcription(
    transcription: TranscriptResult | None,
) -> dict[str, Any] | None:
    if transcription is None:
        return None
    return {
        "text": transcription.text,
        "words": [
            {"word": word.word, "start": word.start, "end": word.end}
            for word in transcription.words
        ],
        "provider": transcription.provider,
        "model": transcription.model,
        "usage": {
            "input_units": transcription.usage.input_units,
            "output_units": transcription.usage.output_units,
        },
        "request_id": transcription.request_id,
        "checksum": transcription.checksum,
        "cost": str(transcription.cost),
        "confidence": transcription.confidence,
        "mode": transcription.mode,
    }


def _deserialize_transcription(value: object) -> TranscriptResult | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CandidateSelectionError("transcription evidence is malformed")
    try:
        words = value["words"]
        usage = value["usage"]
        if not isinstance(words, list) or not isinstance(usage, dict):
            raise CandidateSelectionError("transcription evidence is malformed")
        if not all(isinstance(item, dict) for item in words):
            raise CandidateSelectionError("transcription evidence is malformed")
        return TranscriptResult(
            text=value["text"],
            words=tuple(
                TranscriptWord(
                    word=item["word"],
                    start=float(item["start"]),
                    end=float(item["end"]),
                )
                for item in words
            ),
            provider=value["provider"],
            model=value["model"],
            usage=ProviderUsage(
                input_units=usage["input_units"],
                output_units=usage["output_units"],
            ),
            request_id=value["request_id"],
            checksum=value["checksum"],
            cost=Decimal(str(value["cost"])),
            confidence=(
                float(value["confidence"])
                if value.get("confidence") is not None
                else None
            ),
            mode=value.get("mode", "provider"),
        )
    except (ArithmeticError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, CandidateSelectionError):
            raise
        raise CandidateSelectionError("transcription evidence is malformed") from error


def rank_candidates(
    candidates: tuple[CandidateQuality, ...],
) -> tuple[CandidateQuality, ...]:
    """Return eligible candidates by descending score, breaking ties by ID."""
    _validate_candidates(candidates)
    return tuple(
        sorted(
            (candidate for candidate in candidates if candidate.passes_hard_gates),
            key=lambda candidate: (-candidate.soft_score, candidate.candidate_id),
        )
    )


def select_candidate(
    candidates: tuple[CandidateQuality, ...],
) -> CandidateQuality | None:
    """Return the highest-ranked eligible candidate, if one exists."""
    ranked = rank_candidates(candidates)
    return ranked[0] if ranked else None


def _validate_candidates(candidates: tuple[CandidateQuality, ...]) -> None:
    if not isinstance(candidates, tuple):
        raise CandidateSelectionError("candidates must be a tuple")
    if not all(isinstance(candidate, CandidateQuality) for candidate in candidates):
        raise CandidateSelectionError("candidates must contain CandidateQuality values")


__all__ = [
    "CandidateQuality",
    "CandidateSelectionError",
    "rank_candidates",
    "select_candidate",
]
