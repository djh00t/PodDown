"""BDD bindings for structured reasoning metering and provenance."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from poddown.content.adaptation_envelope import (
    AdaptationEnvelope,
    AdaptationUsage,
    AdaptedTurn,
)
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.reasoning_metering import record_adaptation_evidence
from poddown.content.reasoning_request import ReasoningRequest
from poddown.evidence import ExecutionMode

scenarios("../features/reasoning_metering.feature")


def _values() -> tuple[ReasoningRequest, ReasoningResponse, AdaptationEnvelope]:
    """Build one source-free test record with fixed provider metadata."""
    source_sha256 = "a" * 64
    request = ReasoningRequest(
        source_sha256=source_sha256,
        profile_id="profile-1",
        treatment_id="treatment-1",
        operation="adapt",
        source_blocks=(
            {"block_id": "block-1", "kind": "paragraph", "text": "Source."},
        ),
        speaker_ids=("host",),
    )
    usage = AdaptationUsage({"input_tokens": 12, "output_tokens": 8})
    response = ReasoningResponse(
        text='{"schema_version":"1.0"}',
        model="gpt-5-mini",
        request_id="resp-1",
        usage=usage,
        estimated_cost=Decimal("0.0042"),
    )
    envelope = AdaptationEnvelope(
        source_sha256=source_sha256,
        treatment_id="treatment-1",
        model="gpt-5-mini",
        request_id="resp-1",
        turns=(AdaptedTurn("turn-1", "host", "editorial", "Exactly.", (), ()),),
        usage=usage,
        estimated_cost=Decimal("0.0042"),
    )
    return request, response, envelope


@given("a validated live reasoning response")
def validated_live_response(context) -> None:
    """Provide the exact request, response, and parsed envelope under test."""
    context.values["reasoning_values"] = _values()


@when("I record structured reasoning evidence")
def record_reasoning_evidence(context) -> None:
    """Record the live response using a fixed UTC observation time."""
    request, response, envelope = context.values["reasoning_values"]
    context.values["evidence"] = record_adaptation_evidence(
        request,
        response,
        envelope,
        provider="openai",
        mode=ExecutionMode.LIVE_PROVIDER,
        latency_ms=17,
        retry_count=0,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@then("the evidence is provider-live with the response cost")
def evidence_is_live(context) -> None:
    """Verify provenance, normalized usage, and cost without raw payloads."""
    evidence = context.values["evidence"]
    assert evidence.evidence_kind == "provider-live"
    assert evidence.estimated_cost == Decimal("0.0042")
    assert dict(evidence.usage) == {"input_tokens": 12, "output_tokens": 8}
    assert "schema_version" not in repr(evidence)
