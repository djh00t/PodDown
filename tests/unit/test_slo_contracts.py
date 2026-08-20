"""Unit tests for bounded service-level objective measurements."""

import math

import pytest

from poddown.production_readiness import (
    SloComparison,
    SloObjective,
)


def test_slo_measurement_uses_explicit_comparison_and_json_safe_values() -> None:
    objective = SloObjective(
        name="render.completion",
        comparison=SloComparison.AT_LEAST,
        target=0.95,
        unit="ratio",
    )
    measurement = objective.measure(observed=0.96, sample_count=25)
    assert measurement.met is True
    assert measurement.to_dict() == {
        "comparison": "at_least",
        "met": True,
        "name": "render.completion",
        "observed": 0.96,
        "sample_count": 25,
        "target": 0.95,
        "unit": "ratio",
    }


def test_at_most_objective_fails_when_observation_exceeds_target() -> None:
    objective = SloObjective(
        name="provider.failure",
        comparison=SloComparison.AT_MOST,
        target=0.02,
        unit="ratio",
    )
    assert objective.measure(observed=0.021, sample_count=100).met is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "name": "",
            "comparison": SloComparison.AT_LEAST,
            "target": 0.9,
            "unit": "ratio",
        },
        {
            "name": "api",
            "comparison": SloComparison.AT_LEAST,
            "target": -1,
            "unit": "ratio",
        },
        {
            "name": "api",
            "comparison": SloComparison.AT_LEAST,
            "target": math.inf,
            "unit": "ratio",
        },
        {
            "name": "api",
            "comparison": SloComparison.AT_LEAST,
            "target": 0.9,
            "unit": "",
        },
    ],
)
def test_objective_rejects_invalid_identity_or_target(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        SloObjective(**kwargs)  # type: ignore[arg-type]


def test_measurement_rejects_invalid_observation_or_sample_count() -> None:
    objective = SloObjective(
        name="cost.variance",
        comparison=SloComparison.AT_MOST,
        target=0.1,
        unit="ratio",
    )
    with pytest.raises(ValueError, match="finite"):
        objective.measure(observed=math.nan, sample_count=1)
    with pytest.raises(ValueError, match="sample_count"):
        objective.measure(observed=0.1, sample_count=0)
