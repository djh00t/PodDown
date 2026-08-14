"""Public provider-evidence contract coverage."""

import inspect
from datetime import UTC, datetime
from decimal import Decimal

from uuid6 import uuid7

import poddown.providers.contracts as contracts
from poddown.persistence import UsageEvent


def test_provider_evidence_freezes_the_production_closure_wire_surface() -> None:
    """Catch contract expansion that would retain raw provider request content."""
    evidence_type = getattr(contracts, "ProviderEvidence", None)

    assert evidence_type is not None
    assert tuple(inspect.signature(evidence_type).parameters) == (
        "operation",
        "provider",
        "request_id",
        "model",
        "input_sha256",
        "output_sha256",
        "usage",
        "currency",
        "estimated_cost",
        "reconciled_cost",
        "latency_ms",
        "retry_count",
        "occurred_at",
        "evidence_kind",
    )


def test_transcription_result_constructor_and_alias_remain_compatible() -> None:
    """Catch the new evidence contract changing the established transcription API."""
    assert contracts.TranscriptionResult is contracts.TranscriptResult
    assert tuple(inspect.signature(contracts.TranscriptResult).parameters) == (
        "text",
        "words",
        "provider",
        "model",
        "usage",
        "request_id",
        "checksum",
        "cost",
        "confidence",
        "mode",
    )


def test_provider_evidence_usage_is_accepted_by_the_durable_usage_boundary() -> None:
    """Catch C05 metering metadata that cannot become a durable usage event."""
    evidence = contracts.ProviderEvidence(
        operation="render",
        provider="elevenlabs",
        request_id="request-123",
        model="eleven-multilingual-v2",
        input_sha256="a" * 64,
        output_sha256="b" * 64,
        usage={"input_units": 12, "output_units": 5},
        currency="USD",
        estimated_cost=Decimal("0.0123"),
        reconciled_cost=None,
        latency_ms=420,
        retry_count=0,
        occurred_at=datetime(2026, 8, 11, 12, 30, tzinfo=UTC),
        evidence_kind="provider-live",
    )

    event = UsageEvent(
        tenant_id=uuid7(),
        project_id=uuid7(),
        job_id=uuid7(),
        provider_request_id=evidence.request_id,
        operation=evidence.operation,
        units=evidence.usage,
        currency=evidence.currency,
        estimated_cost=evidence.estimated_cost,
        reconciled_cost=evidence.reconciled_cost,
        created_at=evidence.occurred_at,
    )

    assert dict(event.units) == {"input_units": 12, "output_units": 5}
