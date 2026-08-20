"""Fail-closed provider evidence for structured adaptation reasoning."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

from poddown.content.adaptation_envelope import AdaptationEnvelope, AdaptationUsage
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.reasoning_request import ReasoningRequest
from poddown.evidence import EvidenceKind, ExecutionMode
from poddown.providers.contracts import ProviderEvidence

_NORMALIZED_USAGE_KEYS = frozenset({"input_tokens", "output_tokens"})
_MAX_ESTIMATED_COST = Decimal("999999999.999999")


class ReasoningMeteringError(ValueError):
    """Stable rejection that never exposes source or provider payload text."""

    def __init__(self) -> None:
        super().__init__("reasoning metering rejected")


def record_adaptation_evidence(
    request: ReasoningRequest,
    response: ReasoningResponse,
    envelope: AdaptationEnvelope,
    *,
    provider: str,
    mode: ExecutionMode,
    latency_ms: int,
    retry_count: int,
    occurred_at: datetime,
) -> ProviderEvidence:
    """Freeze one adaptation outcome as hash-only provider evidence."""
    try:
        _validate_inputs(request, response, envelope, provider, mode, occurred_at)
        evidence_kind, estimated_cost = _provenance_and_cost(mode, envelope)
        if not isinstance(response.usage, AdaptationUsage):
            raise TypeError("response usage must be AdaptationUsage")
        return ProviderEvidence(
            operation="adapt",
            provider=provider,
            request_id=response.request_id,
            model=response.model,
            input_sha256=_request_sha256(request),
            output_sha256=_sha256(response.text.encode("utf-8")),
            usage=dict(response.usage.values),
            currency="USD",
            estimated_cost=estimated_cost,
            reconciled_cost=None,
            latency_ms=latency_ms,
            retry_count=retry_count,
            occurred_at=occurred_at,
            evidence_kind=evidence_kind,
        )
    except (TypeError, ValueError):
        raise ReasoningMeteringError() from None


def _validate_inputs(
    request: ReasoningRequest,
    response: ReasoningResponse,
    envelope: AdaptationEnvelope,
    provider: str,
    mode: ExecutionMode,
    occurred_at: datetime,
) -> None:
    if not isinstance(request, ReasoningRequest):
        raise TypeError("request must be a ReasoningRequest")
    if not isinstance(response, ReasoningResponse):
        raise TypeError("response must be a ReasoningResponse")
    if not isinstance(envelope, AdaptationEnvelope):
        raise TypeError("envelope must be an AdaptationEnvelope")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider must be non-empty")
    if not isinstance(mode, ExecutionMode):
        raise ValueError("mode must be an ExecutionMode")
    if not isinstance(occurred_at, datetime) or occurred_at.utcoffset() != timedelta(0):
        raise ValueError("occurred_at must be a UTC datetime")
    if mode is ExecutionMode.LIVE_PROVIDER and provider != "openai":
        raise ValueError("live reasoning must use OpenAI")
    if (
        request.source_sha256 != envelope.source_sha256
        or request.treatment_id != envelope.treatment_id
        or response.model != envelope.model
        or response.request_id != envelope.request_id
        or response.usage != envelope.usage
        or response.estimated_cost != envelope.estimated_cost
    ):
        raise ValueError("adaptation metadata does not match")
    if not isinstance(response.usage, AdaptationUsage):
        raise TypeError("response usage must be AdaptationUsage")
    usage = response.usage.values
    if set(usage) != _NORMALIZED_USAGE_KEYS:
        raise ValueError("adaptation usage is not normalized")
    if any(type(value) is not int or value < 0 for value in usage.values()):
        raise ValueError("adaptation usage is invalid")
    if envelope.estimated_cost > _MAX_ESTIMATED_COST:
        raise ValueError("estimated cost exceeds evidence precision")


def _provenance_and_cost(
    mode: ExecutionMode, envelope: AdaptationEnvelope
) -> tuple[EvidenceKind, Decimal]:
    if mode is ExecutionMode.LIVE_PROVIDER:
        return EvidenceKind.PROVIDER_LIVE, envelope.estimated_cost
    if mode is ExecutionMode.HOST_LOCAL:
        return EvidenceKind.HOST_LOCAL, Decimal("0")
    return EvidenceKind.SYNTHETIC, Decimal("0")


def _request_sha256(request: ReasoningRequest) -> str:
    serialized = json.dumps(
        request.to_record(),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256(serialized)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "ReasoningMeteringError",
    "record_adaptation_evidence",
]
