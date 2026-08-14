"""BDD bindings for normalized provider evidence."""

from datetime import UTC, datetime
from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

import poddown.providers.contracts as contracts

scenarios("../features/provider_evidence.feature")


@given("complete live transcription provider evidence")
def complete_live_transcription_evidence(context) -> None:
    """Provide provider metadata without retaining raw source or audio data."""
    context.values["values"] = {
        "operation": "transcribe",
        "provider": "openai",
        "request_id": "req-123",
        "model": "gpt-4o-transcribe",
        "input_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "usage": {"audio_seconds": 32, "output_tokens": 9},
        "currency": "USD",
        "estimated_cost": Decimal("0.0123"),
        "reconciled_cost": None,
        "latency_ms": 420,
        "retry_count": 1,
        "occurred_at": datetime(2026, 8, 11, 12, 30, tzinfo=UTC),
        "evidence_kind": "provider-live",
    }


@when("the provider evidence is frozen")
def provider_evidence_is_frozen(context) -> None:
    """Construct the public contract dynamically so the RED test can collect."""
    evidence_type = getattr(contracts, "ProviderEvidence", None)
    context.values["evidence"] = (
        evidence_type(**context.values["values"]) if evidence_type is not None else None
    )


@then("the normalized provider evidence is retained without raw payload fields")
def normalized_evidence_is_retained(context) -> None:
    """Require stable metadata and reject accidental raw data expansion."""
    evidence = context.values["evidence"]

    assert evidence is not None
    assert evidence.operation == "transcribe"
    assert evidence.usage == {"audio_seconds": 32, "output_tokens": 9}
    assert evidence.estimated_cost == Decimal("0.0123")
    assert evidence.reconciled_cost is None
    assert evidence.occurred_at == datetime(2026, 8, 11, 12, 30, tzinfo=UTC)
    assert set(evidence.__dataclass_fields__) == {
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
    }
