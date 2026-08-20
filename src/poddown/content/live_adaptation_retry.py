"""One fail-closed schema-repair retry around live adaptation boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from poddown.content.adaptation import (
    AdaptationError,
    AdaptationProposal,
    EpisodeTreatment,
    _validate_turns,
)
from poddown.content.adaptation_envelope import AdaptationEnvelope, AdaptationUsage
from poddown.content.live_adaptation import _parse_matching_envelope
from poddown.content.live_adaptation_repair import LiveAdaptationRepairPort
from poddown.content.models import Profile, ScriptTurn, SourceSnapshot
from poddown.content.openai_reasoning import ReasoningTransport
from poddown.content.reasoning_request import build_reasoning_request

RetryErrorCode = Literal["initial_rejected", "repair_rejected", "local_rejected"]
_REPAIRABLE_ADAPTATION_CODES = frozenset({"dialogue_quality"})


class LiveAdaptationRetryError(ValueError):
    """A terminal adaptation error with safe, observable retry evidence."""

    def __init__(
        self,
        code: RetryErrorCode,
        provider_call_count: int,
        retry_reason: str | None,
    ) -> None:
        self.code = code
        self.provider_call_count = provider_call_count
        self.retry_reason = retry_reason
        super().__init__("live adaptation retry rejected")


@dataclass(frozen=True)
class LiveAdaptationRetryResult:
    """A validated proposal with provider-call and retry-safe evidence."""

    proposal: AdaptationProposal
    provider_call_count: int
    retry_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, AdaptationProposal):
            raise TypeError("proposal must be an AdaptationProposal")
        if type(self.provider_call_count) is not int or self.provider_call_count < 0:
            raise ValueError("provider_call_count must be a non-negative integer")
        if self.retry_reason is not None and (
            not isinstance(self.retry_reason, str) or not self.retry_reason
        ):
            raise ValueError("retry_reason must be a non-empty string or None")


class LiveAdaptationRetryPort:
    """Attempt R04 validation once, then permit one R05 repair for semantics."""

    def __init__(self, transport: ReasoningTransport) -> None:
        self._transport = transport

    async def adapt(
        self, source: SourceSnapshot, profile: Profile, treatment: EpisodeTreatment
    ) -> LiveAdaptationRetryResult:
        """Return a proposal or terminal safe error after at most two calls."""
        try:
            request = build_reasoning_request(source, profile, treatment)
        except (TypeError, ValueError):
            raise LiveAdaptationRetryError("initial_rejected", 0, None) from None

        try:
            response = await self._transport.respond(request)
            if not isinstance(response.usage, AdaptationUsage):
                raise TypeError("reasoning response usage is not normalized")
            envelope = _parse_matching_envelope(
                response.text,
                source.source_sha256,
                treatment.treatment_id,
                response.model,
                response.request_id,
                response.usage,
            )
        except Exception:
            raise LiveAdaptationRetryError("initial_rejected", 1, None) from None

        try:
            proposal = _proposal_from_envelope(source, profile, treatment, envelope)
        except AdaptationError as error:
            if error.code in _REPAIRABLE_ADAPTATION_CODES:
                return await self._repair(
                    source, profile, treatment, envelope, error.code
                )
            raise LiveAdaptationRetryError("initial_rejected", 1, None) from None
        except (TypeError, ValueError):
            raise LiveAdaptationRetryError("initial_rejected", 1, None) from None
        return LiveAdaptationRetryResult(proposal, 1, None)

    @staticmethod
    def accept_local(
        source: SourceSnapshot, profile: Profile, proposal: AdaptationProposal
    ) -> LiveAdaptationRetryResult:
        """Validate an already-local proposal without a provider request."""
        try:
            _validate_turns(
                source,
                profile,
                proposal.turns,
                proposal.treatment.expected_turn_ids,
            )
        except (AdaptationError, TypeError, ValueError):
            raise LiveAdaptationRetryError("local_rejected", 0, None) from None
        return LiveAdaptationRetryResult(proposal, 0, None)

    async def _repair(
        self,
        source: SourceSnapshot,
        profile: Profile,
        treatment: EpisodeTreatment,
        envelope: AdaptationEnvelope,
        retry_reason: str,
    ) -> LiveAdaptationRetryResult:
        try:
            proposal = await LiveAdaptationRepairPort(
                self._transport
            ).repair_failed_envelope(source, profile, treatment, envelope)
        except Exception:
            raise LiveAdaptationRetryError("repair_rejected", 2, retry_reason) from None
        return LiveAdaptationRetryResult(proposal, 2, retry_reason)


def _proposal_from_envelope(
    source: SourceSnapshot,
    profile: Profile,
    treatment: EpisodeTreatment,
    envelope: AdaptationEnvelope,
) -> AdaptationProposal:
    turns = tuple(
        ScriptTurn(
            turn.turn_id,
            turn.speaker_id,
            turn.text,
            turn.kind,
            turn.source_anchors,
            turn.claim_anchors,
        )
        for turn in envelope.turns
    )
    _validate_turns(source, profile, turns, treatment.expected_turn_ids)
    return AdaptationProposal(treatment, turns)
