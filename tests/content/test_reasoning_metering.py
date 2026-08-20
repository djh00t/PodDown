"""Unit coverage for fail-closed adaptation provider metering."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from poddown.content.adaptation_envelope import AdaptationEnvelope, AdaptationUsage
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.reasoning_request import ReasoningRequest
from poddown.evidence import EvidenceKind, ExecutionMode


def _request() -> ReasoningRequest:
    return ReasoningRequest(
        source_sha256="a" * 64,
        treatment_id="treatment-001",
        profile={},
        treatment={},
        source_anchors=(),
    )


def _response(*, usage: AdaptationUsage | None = None) -> ReasoningResponse:
    return ReasoningResponse(
        text='{"turns":[]}',
        model="gpt-4.1-mini",
        request_id="resp-001",
        usage=usage or AdaptationUsage({"input_tokens": 8, "output_tokens": 5}),
    )


def _envelope(
    *,
    usage: AdaptationUsage | None = None,
    model: str = "gpt-4.1-mini",
    estimated_cost: Decimal = Decimal("0.00125"),
) -> AdaptationEnvelope:
    return AdaptationEnvelope(
        source_sha256="a" * 64,
        treatment_id="treatment-001",
        model=model,
        request_id="resp-001",
        turns=(),
        usage=usage or AdaptationUsage({"input_tokens": 8, "output_tokens": 5}),
        estimated_cost=estimated_cost,
    )


def test_live_adaptation_records_normalized_provider_evidence() -> None:
    """A matching live response keeps only auditable hashes and metadata."""
    from poddown.content.reasoning_metering import record_adaptation_evidence

    evidence = record_adaptation_evidence(
        _request(),
        _response(),
        _envelope(),
        provider="openai",
        mode=ExecutionMode.LIVE_PROVIDER,
        latency_ms=420,
        retry_count=1,
        occurred_at=datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
    )

    assert evidence.operation == "adapt"
    assert evidence.provider == "openai"
    assert evidence.model == "gpt-4.1-mini"
    assert evidence.request_id == "resp-001"
    assert evidence.usage == {"input_tokens": 8, "output_tokens": 5}
    assert evidence.estimated_cost == Decimal("0.00125")
    assert evidence.evidence_kind == "provider-live"
    assert (
        evidence.input_sha256
        == "f99ea7001eed3cd42cb0b0a42893bb2c553ceb18552a43dd0f036b896d05d21e"
    )
    assert (
        evidence.output_sha256
        == "d5fb095584e9f878eda4919412601f834c18fc24b3310d16ce21830a025a95f8"
    )
    assert not hasattr(evidence, "prompt")
    assert not hasattr(evidence, "payload")


@pytest.mark.parametrize(
    ("response", "envelope", "occurred_at"),
    [
        (
            _response(
                usage=AdaptationUsage(
                    {"input_tokens": 8, "output_tokens": 5, "total_tokens": 13}
                )
            ),
            _envelope(
                usage=AdaptationUsage(
                    {"input_tokens": 8, "output_tokens": 5, "total_tokens": 13}
                )
            ),
            datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
        ),
        (
            _response(),
            _envelope(model="other-model"),
            datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
        ),
        (
            _response(),
            _envelope(),
            datetime(2026, 8, 12, 9, 30, tzinfo=timezone(timedelta(hours=10))),
        ),
        (
            _response(),
            _envelope(estimated_cost=Decimal("1000000000")),
            datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
        ),
    ],
)
def test_invalid_adaptation_metering_fails_closed(
    response: ReasoningResponse,
    envelope: AdaptationEnvelope,
    occurred_at: datetime,
) -> None:
    """Unknown usage, mismatches, non-UTC time, and cost overflow are rejected."""
    from poddown.content.reasoning_metering import (
        ReasoningMeteringError,
        record_adaptation_evidence,
    )

    with pytest.raises(ReasoningMeteringError, match="reasoning metering rejected"):
        record_adaptation_evidence(
            _request(),
            response,
            envelope,
            provider="openai",
            mode=ExecutionMode.LIVE_PROVIDER,
            latency_ms=420,
            retry_count=1,
            occurred_at=occurred_at,
        )


@pytest.mark.parametrize(
    "usage_values",
    [
        {"input_tokens": -1, "output_tokens": 5},
        {"input_tokens": "8", "output_tokens": 5},
    ],
)
def test_negative_or_malformed_usage_fails_closed(
    usage_values: dict[str, int | str],
) -> None:
    """The evidence boundary revalidates corrupted normalized usage."""
    from poddown.content.reasoning_metering import (
        ReasoningMeteringError,
        record_adaptation_evidence,
    )

    usage = AdaptationUsage({"input_tokens": 8, "output_tokens": 5})
    object.__setattr__(usage, "values", usage_values)

    with pytest.raises(ReasoningMeteringError, match="reasoning metering rejected"):
        record_adaptation_evidence(
            _request(),
            _response(usage=usage),
            _envelope(usage=usage),
            provider="openai",
            mode=ExecutionMode.LIVE_PROVIDER,
            latency_ms=420,
            retry_count=1,
            occurred_at=datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("mode", "expected_kind"),
    [
        (ExecutionMode.DETERMINISTIC_LOCAL, EvidenceKind.SYNTHETIC),
        (ExecutionMode.HOST_LOCAL, EvidenceKind.HOST_LOCAL),
    ],
)
def test_local_modes_record_only_zero_cost_local_evidence(
    mode: ExecutionMode, expected_kind: EvidenceKind
) -> None:
    """A local run cannot be represented as a billable live provider call."""
    from poddown.content.reasoning_metering import record_adaptation_evidence

    evidence = record_adaptation_evidence(
        _request(),
        _response(),
        _envelope(),
        provider="local",
        mode=mode,
        latency_ms=0,
        retry_count=0,
        occurred_at=datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
    )

    assert evidence.evidence_kind == expected_kind
    assert evidence.estimated_cost == Decimal("0")
