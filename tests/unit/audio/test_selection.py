"""Contract tests for hard-gated, deterministic candidate selection."""

from decimal import Decimal

from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.selection import CandidateQuality, rank_candidates, select_candidate
from poddown.domain import FidelityResult


def quality(
    candidate_id: str,
    *,
    fidelity_passed: bool = True,
    pronunciation_passed: bool = True,
    soft_score: str = "0.80",
) -> CandidateQuality:
    return CandidateQuality(
        candidate_id=candidate_id,
        fidelity=FidelityResult(
            fidelity_passed, 1.0 if fidelity_passed else 0.5, "none" if fidelity_passed else "segment"
        ),
        diagnostics=AudioDiagnostics(
            sample_rate_hz=44_100,
            channels=1,
            duration_seconds=1.0,
            peak_amplitude=0.5,
            clipping_ratio=0.0,
            silence_ratio=0.1,
        ),
        pronunciation_passed=pronunciation_passed,
        soft_score=Decimal(soft_score),
    )


def test_hard_gate_failure_cannot_be_rescued_by_a_higher_soft_score():
    rejected = quality("candidate-rejected", fidelity_passed=False, soft_score="0.99")
    accepted = quality("candidate-accepted", soft_score="0.40")

    assert select_candidate((rejected, accepted)) == accepted


def test_pronunciation_failure_is_rejected_before_soft_ranking():
    rejected = quality("candidate-rejected", pronunciation_passed=False, soft_score="0.99")
    accepted = quality("candidate-accepted", soft_score="0.40")

    assert select_candidate((rejected, accepted)) == accepted


def test_passing_candidates_are_ranked_by_stable_weighted_score_then_id():
    candidates = (
        quality("candidate-z", soft_score="0.80"),
        quality("candidate-a", soft_score="0.80"),
        quality("candidate-m", soft_score="0.90"),
    )

    assert [candidate.candidate_id for candidate in rank_candidates(candidates)] == [
        "candidate-m",
        "candidate-a",
        "candidate-z",
    ]
    assert select_candidate(candidates).candidate_id == "candidate-m"


def test_all_hard_gate_failures_produce_no_selection():
    candidates = (
        quality("candidate-fidelity", fidelity_passed=False),
        quality("candidate-pronunciation", pronunciation_passed=False),
    )

    assert select_candidate(candidates) is None
