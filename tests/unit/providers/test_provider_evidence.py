"""Unit coverage for frozen provider evidence validation."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import poddown.providers.contracts as contracts


def _values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "operation": "render",
        "provider": "elevenlabs",
        "request_id": "req-123",
        "model": "eleven-multilingual-v2",
        "input_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "usage": {"input_characters": 200},
        "currency": "USD",
        "estimated_cost": Decimal("0.0123"),
        "reconciled_cost": Decimal("0.0100"),
        "latency_ms": 420,
        "retry_count": 1,
        "occurred_at": datetime(2026, 8, 11, 12, 30, tzinfo=UTC),
        "evidence_kind": "provider-live",
    }
    values.update(overrides)
    return values


def _build(**overrides: object) -> object:
    evidence_type = getattr(contracts, "ProviderEvidence", None)
    assert evidence_type is not None, "ProviderEvidence must be publicly available"
    return evidence_type(**_values(**overrides))


def test_provider_evidence_freezes_normalized_metadata() -> None:
    """Catch provider evidence losing a normalized field or mutable usage mapping."""
    usage = {"input_characters": 200}
    evidence = _build(usage=usage)
    usage["input_characters"] = 0

    assert evidence.operation == "render"
    assert evidence.usage == {"input_characters": 200}
    with pytest.raises(TypeError):
        evidence.usage["input_characters"] = 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("operation", "unknown", "operation"),
        ("evidence_kind", "unknown", "evidence_kind"),
        ("provider", "", "provider"),
        ("request_id", "", "request_id"),
        ("model", "", "model"),
        ("currency", "", "currency"),
        ("input_sha256", "not-a-hash", "input_sha256"),
        ("output_sha256", "A" * 64, "output_sha256"),
        ("usage", {}, "usage"),
        ("usage", {"input_characters": -1}, "usage"),
        ("usage", {"input_characters": True}, "usage"),
        ("estimated_cost", Decimal("-0.01"), "estimated_cost"),
        ("estimated_cost", Decimal("NaN"), "estimated_cost"),
        ("reconciled_cost", Decimal("Infinity"), "reconciled_cost"),
        ("latency_ms", -1, "latency_ms"),
        ("latency_ms", True, "latency_ms"),
        ("retry_count", -1, "retry_count"),
        ("retry_count", True, "retry_count"),
        (
            "occurred_at",
            datetime(2026, 8, 11, 12, 30, tzinfo=timezone(timedelta(hours=1))),
            "occurred_at",
        ),
        ("occurred_at", datetime(2026, 8, 11, 12, 30), "occurred_at"),
    ],
)
def test_provider_evidence_rejects_malformed_metadata(
    field: str, value: object, message: str
) -> None:
    """Catch malformed provider metadata being accepted into durable evidence."""
    with pytest.raises(ValueError, match=message):
        _build(**{field: value})


@pytest.mark.parametrize("field", ("payload", "source", "audio"))
def test_provider_evidence_rejects_raw_data_fields(field: str) -> None:
    """Catch raw provider payloads or inputs being retained in the contract."""
    with pytest.raises(TypeError):
        _build(**{field: b"raw-provider-data"})
