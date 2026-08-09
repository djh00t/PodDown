"""Contract tests for hard-gated, deterministic candidate selection."""

from dataclasses import replace
from decimal import Decimal

import pytest

from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.selection import (
    CandidateQuality,
    CandidateSelectionError,
    rank_candidates,
    select_candidate,
)
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
            fidelity_passed,
            1.0 if fidelity_passed else 0.5,
            "none" if fidelity_passed else "segment",
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
    rejected = quality(
        "candidate-rejected", pronunciation_passed=False, soft_score="0.99"
    )
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


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_id": ""},
        {"fidelity": object()},
        {"diagnostics": object()},
        {"pronunciation_passed": 1},
        {"soft_score": Decimal("NaN")},
    ],
)
def test_candidate_contract_rejects_invalid_fields(overrides: dict[str, object]):
    values: dict[str, object] = {
        "candidate_id": "candidate-1",
        "fidelity": FidelityResult(True, 1.0, "none"),
        "diagnostics": AudioDiagnostics(44_100, 1, 1.0, 0.5, 0.0, 0.1),
        "pronunciation_passed": True,
        "soft_score": Decimal("0.80"),
    }
    values.update(overrides)

    with pytest.raises(CandidateSelectionError):
        CandidateQuality(**values)  # type: ignore[arg-type]


def test_candidate_quality_round_trips_through_activity_payload():
    candidate = quality("candidate-1", soft_score="0.73")

    assert CandidateQuality.from_dict(candidate.to_dict()) == candidate


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"fidelity": {}, "diagnostics": {}, "soft_score": "not-a-decimal"},
    ],
)
def test_malformed_activity_quality_is_rejected(payload):
    with pytest.raises(CandidateSelectionError):
        CandidateQuality.from_dict(payload)  # type: ignore[arg-type]


def test_audio_diagnostic_failure_is_a_hard_gate():
    clipped = replace(
        quality("candidate-clipped"),
        diagnostics=AudioDiagnostics(44_100, 1, 1.0, 1.0, 1.0, 0.1),
    )

    assert select_candidate((clipped,)) is None


@pytest.mark.parametrize(
    "candidates", [[quality("candidate-1")], (quality("candidate-1"), "bad")]
)
def test_candidate_collection_must_be_a_tuple_of_quality_values(candidates):
    with pytest.raises(CandidateSelectionError):
        rank_candidates(candidates)  # type: ignore[arg-type]
