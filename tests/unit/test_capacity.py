"""Unit tests for deterministic bounded load admission."""

from decimal import Decimal

import pytest

from poddown.capacity import LoadContract


def _contract() -> LoadContract:
    return LoadContract(
        max_in_flight=2,
        max_queue_age_seconds=60,
        max_estimated_cost=Decimal("1.00"),
    )


def test_admission_is_within_bounds_and_serializes_safe_evidence() -> None:
    decision = _contract().check(
        in_flight=1,
        queue_age_seconds=60,
        estimated_cost=Decimal("1.00"),
    )
    assert decision.admitted is True
    assert decision.to_dict() == {
        "admitted": True,
        "estimated_cost": "1.00",
        "in_flight": 1,
        "queue_age_seconds": 60.0,
        "reason": "within capacity bounds",
    }


@pytest.mark.parametrize(
    ("in_flight", "queue_age_seconds", "estimated_cost", "reason"),
    [
        (2, 1, Decimal("0.10"), "concurrency limit reached"),
        (1, 61, Decimal("0.10"), "queue age limit exceeded"),
        (1, 1, Decimal("1.01"), "estimated cost exceeds ceiling"),
    ],
)
def test_admission_rejects_each_bound_with_stable_reason(
    in_flight: int,
    queue_age_seconds: float,
    estimated_cost: Decimal,
    reason: str,
) -> None:
    decision = _contract().check(
        in_flight=in_flight,
        queue_age_seconds=queue_age_seconds,
        estimated_cost=estimated_cost,
    )
    assert decision.admitted is False
    assert decision.reason == reason


def test_invalid_limits_and_observations_fail_closed() -> None:
    with pytest.raises(ValueError, match="max_in_flight"):
        LoadContract(
            max_in_flight=0,
            max_queue_age_seconds=60,
            max_estimated_cost=Decimal("1"),
        )
    with pytest.raises(ValueError, match="Decimal"):
        _contract().check(
            in_flight=1,
            queue_age_seconds=1,
            estimated_cost=1.0,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="non-negative"):
        _contract().check(
            in_flight=-1,
            queue_age_seconds=1,
            estimated_cost=Decimal("0.1"),
        )
