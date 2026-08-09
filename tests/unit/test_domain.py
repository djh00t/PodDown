"""Tests for immutable domain results."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from poddown.domain import (
    CandidateResult,
    FidelityResult,
    ProviderUsage,
    SourceValidation,
)


@pytest.mark.parametrize(
    ("result", "field", "value"),
    [
        (ProviderUsage(1, 2), "input_units", 3),
        (SourceValidation(True, "sha256", ()), "accepted", False),
        (FidelityResult(True, 1.0, "none"), "accuracy", 0.5),
        (CandidateResult(True, 1, cost=Decimal("0.01")), "accepted", False),
    ],
)
def test_domain_results_reject_mutation(result, field, value):
    """A caller cannot mutate persisted result values after creation."""
    with pytest.raises(FrozenInstanceError):
        setattr(result, field, value)
