"""Unit contracts for PostgreSQL provider evidence persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from uuid6 import uuid7

from poddown.persistence import UsageEvent
from poddown.providers.contracts import ProviderEvidence


def _event() -> UsageEvent:
    return UsageEvent(
        tenant_id=UUID("018f2c8b-7b46-7cc5-b2e1-111111111111"),
        project_id=UUID("018f2c8b-7b46-7cc5-b2e1-222222222222"),
        job_id=uuid7(),
        provider_request_id="request-1",
        operation="render",
        units={"characters": 12},
        currency="USD",
        estimated_cost=Decimal("0.12"),
        reconciled_cost=None,
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )


def test_usage_event_identity_is_uuidv7_scoped() -> None:
    with pytest.raises(ValueError, match="UUIDv7"):
        UsageEvent(
            tenant_id=UUID("00000000-0000-4000-8000-000000000000"),
            project_id=_event().project_id,
            job_id=_event().job_id,
            provider_request_id="request-1",
            operation="render",
            units={"characters": 1},
            currency="USD",
            estimated_cost=Decimal("0"),
            reconciled_cost=None,
            created_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


def test_provider_evidence_rejects_non_utc_timestamp() -> None:
    with pytest.raises(ValueError, match="UTC"):
        ProviderEvidence(
            operation="render",
            provider="elevenlabs",
            request_id="request-1",
            model="model-1",
            input_sha256="a" * 64,
            output_sha256="b" * 64,
            usage={"characters": 1},
            currency="USD",
            estimated_cost=Decimal("0"),
            reconciled_cost=None,
            latency_ms=1,
            retry_count=0,
            occurred_at=datetime(2026, 8, 14),
            evidence_kind="provider-live",
        )
