"""Immutable values shared by PodDown application services."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ProviderUsage:
    """Normalized billable usage reported by a provider."""

    input_units: int
    output_units: int


@dataclass(frozen=True)
class SourceValidation:
    """Result of validating a canonical Markdown source document."""

    accepted: bool
    source_sha256: str | None
    errors: tuple[str, ...]
    provider_calls: int = 0


@dataclass(frozen=True)
class FidelityResult:
    """Critical-token fidelity decision for one rendered segment."""

    passed: bool
    accuracy: float
    rerender_scope: Literal["none", "segment"]


@dataclass(frozen=True)
class CandidateResult:
    """Normalized outcome of a provider render request."""

    accepted: bool
    provider_calls: int
    provider: str | None = None
    candidate_id: str | None = None
    submitted_text: str | None = None
    request_id: str | None = None
    model: str | None = None
    usage: ProviderUsage | None = None
    cost: Decimal = Decimal("0")
    checksum: str | None = None
    required_gates: frozenset[str] = frozenset()
