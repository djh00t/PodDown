"""Hard-gated, deterministic ranking for rendered audio candidates."""

from dataclasses import dataclass
from decimal import Decimal

from poddown.audio.diagnostics import AudioDiagnostics
from poddown.domain import FidelityResult


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

    @property
    def passes_hard_gates(self) -> bool:
        """Return whether required fidelity and pronunciation gates both passed."""
        return self.fidelity.passed and self.pronunciation_passed


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
