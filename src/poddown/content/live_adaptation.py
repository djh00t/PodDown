"""Explicit live structured-adaptation orchestration and evidence binding."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter_ns

from poddown.content.adaptation import (
    AdaptationError,
    AdaptationProposal,
    EpisodeTreatment,
    adapt_source,
    repair_turn,
)
from poddown.content.adaptation_envelope import (
    AdaptationEnvelope,
    AdaptationUsage,
    parse_adaptation_envelope,
)
from poddown.content.models import Profile, ScriptTurn, ScriptVersion, SourceSnapshot
from poddown.content.openai_reasoning import ReasoningResponse, ReasoningTransport
from poddown.content.reasoning_metering import (
    ReasoningMeteringError,
    record_adaptation_evidence,
)
from poddown.content.reasoning_request import ReasoningRequest, build_reasoning_request
from poddown.evidence import ExecutionMode
from poddown.providers.contracts import ProviderEvidence

_REQUIRED_USAGE_KEYS = frozenset({"input_tokens", "output_tokens"})


class LiveAdaptationError(ValueError):
    """Safe classification for malformed or policy-failing live adaptation."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("adaptation error code must be non-empty")
        self.code = code
        self.retryable = retryable
        super().__init__(f"live adaptation failed: {code}")


ReasoningEvidenceRecorder = Callable[[ProviderEvidence], object]


@dataclass(frozen=True, slots=True)
class _ProposalPort:
    """Adaptation-port bridge for one already parsed provider envelope."""

    proposal: AdaptationProposal

    def adapt(
        self, source: SourceSnapshot, profile: Profile, treatment: EpisodeTreatment
    ) -> AdaptationProposal:
        del source, profile, treatment
        return self.proposal

    def repair(
        self,
        source: SourceSnapshot,
        profile: Profile,
        turn: ScriptTurn,
        failure: str,
    ) -> ScriptTurn:
        del source, profile, turn, failure
        raise LiveAdaptationError("repair_requires_explicit_request")


class LiveAdaptationService:
    """Run source-bound adaptation through an explicit injected transport."""

    def __init__(
        self,
        transport: ReasoningTransport,
        *,
        evidence_recorder: ReasoningEvidenceRecorder | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_cost: Decimal | None = None,
    ) -> None:
        if not hasattr(transport, "respond"):
            raise ValueError("reasoning transport must expose respond(request)")
        if max_cost is not None and (
            not isinstance(max_cost, Decimal)
            or not max_cost.is_finite()
            or max_cost <= 0
        ):
            raise ValueError("max_cost must be a positive Decimal")
        self._transport = transport
        self._evidence_recorder = evidence_recorder
        self._clock = clock
        self._max_cost = max_cost

    async def adapt(
        self,
        source: SourceSnapshot,
        profile: Profile,
        treatment: EpisodeTreatment,
    ) -> ScriptVersion:
        """Adapt once and fail closed on malformed or unsupported output."""
        request = build_reasoning_request(source, profile, treatment)
        response, envelope, latency_ms = await self._request_envelope(request)
        if envelope.source_sha256 != source.source_sha256:
            raise LiveAdaptationError("source_hash_mismatch")
        if envelope.treatment_id != treatment.treatment_id:
            raise LiveAdaptationError("treatment_mismatch")
        if (
            envelope.model != response.model
            or envelope.request_id != response.request_id
        ):
            raise LiveAdaptationError("provider_metadata_mismatch")
        if (
            envelope.usage != response.usage
            or envelope.estimated_cost != response.estimated_cost
        ):
            raise LiveAdaptationError("provider_metering_mismatch")
        self._record_evidence(request, response, envelope, latency_ms)
        turns = tuple(turn.to_script_turn() for turn in envelope.turns)
        proposal = AdaptationProposal(treatment=treatment, turns=turns)
        try:
            result = adapt_source(source, profile, treatment, _ProposalPort(proposal))
        except AdaptationError as error:
            # Unsupported claims and all source/policy failures are terminal.
            raise LiveAdaptationError(error.code) from error
        return result

    async def repair(
        self,
        source: SourceSnapshot,
        profile: Profile,
        treatment: EpisodeTreatment,
        script: ScriptVersion,
        turn_id: str,
        failure_code: str,
        *,
        repair_count: int = 0,
    ) -> ScriptVersion:
        """Apply at most one schema-preserving dialogue-quality repair."""
        if failure_code != "dialogue_quality":
            raise LiveAdaptationError("repair_not_permitted")
        if type(repair_count) is not int or repair_count != 0:
            raise LiveAdaptationError("repair_budget_exhausted")
        if not isinstance(script, ScriptVersion):
            raise ValueError("script must be ScriptVersion")
        request = build_reasoning_request(
            source,
            profile,
            treatment,
            operation="repair",
            repair_turn_id=turn_id,
            failure_code=failure_code,
        )
        response, envelope, latency_ms = await self._request_envelope(request)
        if (
            envelope.source_sha256 != source.source_sha256
            or envelope.treatment_id != treatment.treatment_id
        ):
            raise LiveAdaptationError("repair_identity_mismatch")
        if (
            envelope.model != response.model
            or envelope.request_id != response.request_id
        ):
            raise LiveAdaptationError("provider_metadata_mismatch")
        if (
            envelope.usage != response.usage
            or envelope.estimated_cost != response.estimated_cost
        ):
            raise LiveAdaptationError("provider_metering_mismatch")
        if len(envelope.turns) != 1 or envelope.turns[0].turn_id != turn_id:
            raise LiveAdaptationError("repair_turn_identity_mismatch")
        replacement = envelope.turns[0].to_script_turn()
        try:
            result = repair_turn(script, turn_id, replacement, source, profile)
        except AdaptationError as error:
            raise LiveAdaptationError(error.code) from error
        self._record_evidence(request, response, envelope, latency_ms)
        return result

    async def _request_envelope(
        self, request: ReasoningRequest
    ) -> tuple[ReasoningResponse, AdaptationEnvelope, int]:
        started = perf_counter_ns()
        try:
            response = await self._transport.respond(request)
            payload = json.loads(response.text)
            envelope = parse_adaptation_envelope(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise LiveAdaptationError("malformed_provider_response") from error
        if self._max_cost is not None and response.estimated_cost > self._max_cost:
            raise LiveAdaptationError("cost_ceiling_exceeded")
        elapsed_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        return response, envelope, elapsed_ms

    def _record_evidence(
        self,
        request: ReasoningRequest,
        response: ReasoningResponse,
        envelope: AdaptationEnvelope,
        latency_ms: int,
    ) -> None:
        if self._evidence_recorder is None:
            return
        try:
            evidence = record_adaptation_evidence(
                request,
                response,
                envelope,
                provider="openai",
                mode=ExecutionMode.LIVE_PROVIDER,
                latency_ms=latency_ms,
                retry_count=0,
                occurred_at=self._clock(),
            )
        except ReasoningMeteringError as error:
            raise LiveAdaptationError("provider_metering_failed") from error
        try:
            self._evidence_recorder(evidence)
        except Exception as error:
            raise LiveAdaptationError("evidence_persistence_failed") from error


def _parse_matching_envelope(
    text: str,
    source_sha256: str,
    treatment_id: str,
    model: str,
    request_id: str,
    usage: AdaptationUsage,
) -> AdaptationEnvelope:
    """Parse a provider response and bind every identity and usage field."""
    try:
        record = json.loads(text)
        if not isinstance(record, Mapping):
            raise ValueError("adaptation envelope must be an object")
        envelope = parse_adaptation_envelope(record)
        if (
            envelope.source_sha256 != source_sha256
            or envelope.treatment_id != treatment_id
            or envelope.model != model
            or envelope.request_id != request_id
            or envelope.usage != usage
            or not _REQUIRED_USAGE_KEYS.issubset(envelope.usage.values)
        ):
            raise ValueError("adaptation response metadata does not match")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise LiveAdaptationError("malformed_provider_response") from error
    return envelope


__all__ = [
    "LiveAdaptationError",
    "LiveAdaptationService",
    "ReasoningEvidenceRecorder",
]
