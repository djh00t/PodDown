"""Unit tests for fail-closed structured reasoning metering."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from poddown.content.adaptation_envelope import (
    AdaptationEnvelope,
    AdaptationUsage,
    AdaptedTurn,
)
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.reasoning_metering import (
    ReasoningMeteringError,
    record_adaptation_evidence,
)
from poddown.content.reasoning_request import ReasoningRequest
from poddown.evidence import ExecutionMode


def _values() -> tuple[ReasoningRequest, ReasoningResponse, AdaptationEnvelope]:
    """Build matching request, response, and envelope values."""
    source_sha256 = "b" * 64
    request = ReasoningRequest(
        source_sha256,
        "profile-1",
        "treatment-1",
        "adapt",
        ({"block_id": "block-1", "text": "Source."},),
        ("host",),
    )
    usage = AdaptationUsage({"input_tokens": 2, "output_tokens": 3})
    response = ReasoningResponse("{}", "gpt-5-mini", "resp-1", usage, Decimal("0.001"))
    envelope = AdaptationEnvelope(
        source_sha256,
        "treatment-1",
        "gpt-5-mini",
        "resp-1",
        (AdaptedTurn("turn-1", "host", "editorial", "Exactly.", (), ()),),
        usage,
        Decimal("0.001"),
    )
    return request, response, envelope


def _record(mode: ExecutionMode):
    request, response, envelope = _values()
    return record_adaptation_evidence(
        request,
        response,
        envelope,
        provider="openai" if mode is ExecutionMode.LIVE_PROVIDER else "fixture",
        mode=mode,
        latency_ms=0,
        retry_count=0,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_local_reasoning_evidence_is_synthetic_and_free() -> None:
    evidence = _record(ExecutionMode.DETERMINISTIC_LOCAL)

    assert evidence.evidence_kind == "synthetic"
    assert evidence.estimated_cost == Decimal("0")


def test_live_reasoning_evidence_requires_normalized_usage() -> None:
    request, response, envelope = _values()
    bad_response = ReasoningResponse(
        response.text,
        response.model,
        response.request_id,
        {"input_tokens": 2, "output_tokens": 3, "cached_tokens": 1},
        response.estimated_cost,
    )

    with pytest.raises(ReasoningMeteringError, match="reasoning metering rejected"):
        record_adaptation_evidence(
            request,
            bad_response,
            envelope,
            provider="openai",
            mode=ExecutionMode.LIVE_PROVIDER,
            latency_ms=0,
            retry_count=0,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_mismatched_envelope_is_rejected_without_source_details() -> None:
    request, response, envelope = _values()
    mismatched = AdaptationEnvelope(
        envelope.source_sha256,
        envelope.treatment_id,
        envelope.model,
        envelope.request_id,
        envelope.turns,
        envelope.usage,
        Decimal("0.002"),
    )

    with pytest.raises(ReasoningMeteringError) as error:
        record_adaptation_evidence(
            request,
            response,
            mismatched,
            provider="openai",
            mode=ExecutionMode.LIVE_PROVIDER,
            latency_ms=0,
            retry_count=0,
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert "b" * 64 not in str(error.value)
