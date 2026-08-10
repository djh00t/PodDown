"""Unit contracts for durable persistence and metering value objects."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from poddown.persistence import UsageEvent

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
JOB_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")
CREATED_AT = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)


def test_usage_event_freezes_units_and_serializes_decimal_costs() -> None:
    event = UsageEvent(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        job_id=JOB_ID,
        provider_request_id="provider-request-1",
        operation="render",
        units={"input_tokens": 12, "output_bytes": 48},
        currency="USD",
        estimated_cost=Decimal("0.0040"),
        reconciled_cost=Decimal("0.0035"),
        created_at=CREATED_AT,
    )

    assert dict(event.units) == {"input_tokens": 12, "output_bytes": 48}
    assert event.to_dict() == {
        "tenant_id": str(TENANT_ID),
        "project_id": str(PROJECT_ID),
        "job_id": str(JOB_ID),
        "provider_request_id": "provider-request-1",
        "operation": "render",
        "units": {"input_tokens": 12, "output_bytes": 48},
        "currency": "USD",
        "estimated_cost": "0.0040",
        "reconciled_cost": "0.0035",
        "created_at": CREATED_AT.isoformat(),
    }
    with pytest.raises(TypeError):
        event.units["input_tokens"] = 99  # type: ignore[index]


@pytest.mark.parametrize(
    "values",
    [
        {"tenant_id": uuid4()},
        {"units": {"input_tokens": -1}},
        {"units": {"input_tokens": True}},
        {"currency": "usd"},
        {"estimated_cost": Decimal("-0.01")},
        {"estimated_cost": Decimal("NaN")},
    ],
)
def test_usage_event_rejects_malformed_metering(values: dict[str, object]) -> None:
    base: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "job_id": JOB_ID,
        "provider_request_id": "provider-request-1",
        "operation": "render",
        "units": {"input_tokens": 12},
        "currency": "USD",
        "estimated_cost": Decimal("0.0040"),
        "reconciled_cost": None,
        "created_at": CREATED_AT,
    }
    with pytest.raises(ValueError):
        UsageEvent(**{**base, **values})  # type: ignore[arg-type]
