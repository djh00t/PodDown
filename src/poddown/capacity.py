"""Deterministic load-admission and cost-boundary contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal


def _require_positive_int(value: int, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _require_non_negative_int(value: int, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _require_non_negative_float(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return normalized


def _require_decimal(value: Decimal, *, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite non-negative Decimal")
    return value


@dataclass(frozen=True, slots=True)
class CapacityDecision:
    """Safe result of one load-admission check."""

    admitted: bool
    reason: str
    in_flight: int
    queue_age_seconds: float
    estimated_cost: Decimal

    def to_dict(self) -> dict[str, object]:
        """Return bounded, JSON-safe admission evidence."""
        return {
            "admitted": self.admitted,
            "estimated_cost": format(self.estimated_cost, "f"),
            "in_flight": self.in_flight,
            "queue_age_seconds": self.queue_age_seconds,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LoadContract:
    """Admission limits for concurrency, queue age, and estimated spend."""

    max_in_flight: int
    max_queue_age_seconds: float
    max_estimated_cost: Decimal

    def __post_init__(self) -> None:
        _require_positive_int(self.max_in_flight, field="max_in_flight")
        queue_age = _require_non_negative_float(
            self.max_queue_age_seconds,
            field="max_queue_age_seconds",
        )
        max_cost = _require_decimal(
            self.max_estimated_cost,
            field="max_estimated_cost",
        )
        object.__setattr__(self, "max_queue_age_seconds", queue_age)
        object.__setattr__(self, "max_estimated_cost", max_cost)

    def check(
        self,
        *,
        in_flight: int,
        queue_age_seconds: float,
        estimated_cost: Decimal,
    ) -> CapacityDecision:
        """Return a stable admission decision without dispatching work."""
        normalized_in_flight = _require_non_negative_int(
            in_flight,
            field="in_flight",
        )
        normalized_queue_age = _require_non_negative_float(
            queue_age_seconds,
            field="queue_age_seconds",
        )
        normalized_cost = _require_decimal(estimated_cost, field="estimated_cost")
        reason = "within capacity bounds"
        admitted = True
        if normalized_in_flight >= self.max_in_flight:
            admitted = False
            reason = "concurrency limit reached"
        elif normalized_queue_age > self.max_queue_age_seconds:
            admitted = False
            reason = "queue age limit exceeded"
        elif normalized_cost > self.max_estimated_cost:
            admitted = False
            reason = "estimated cost exceeds ceiling"
        return CapacityDecision(
            admitted=admitted,
            reason=reason,
            in_flight=normalized_in_flight,
            queue_age_seconds=normalized_queue_age,
            estimated_cost=normalized_cost,
        )


__all__ = ["CapacityDecision", "LoadContract"]
