"""One fail-closed repair of a semantically failed adaptation envelope."""

from __future__ import annotations

from poddown.content.adaptation import (
    AdaptationError,
    AdaptationProposal,
    EpisodeTreatment,
    _validate_turns,
)
from poddown.content.adaptation_envelope import AdaptationEnvelope, AdaptationUsage
from poddown.content.live_adaptation import _parse_matching_envelope
from poddown.content.models import Profile, ScriptTurn, SourceSnapshot
from poddown.content.openai_reasoning import ReasoningTransport
from poddown.content.reasoning_request import build_reasoning_request


class LiveAdaptationRepairError(ValueError):
    """A stable repair rejection that does not include provider payloads."""

    def __init__(self) -> None:
        super().__init__("live adaptation repair rejected")


class LiveAdaptationRepairPort:
    """Repair one failed envelope through R03 while retaining its boundaries."""

    def __init__(self, transport: ReasoningTransport) -> None:
        self._transport = transport

    async def repair_failed_envelope(
        self,
        source: SourceSnapshot,
        profile: Profile,
        treatment: EpisodeTreatment,
        failed: AdaptationEnvelope,
    ) -> AdaptationProposal:
        """Return one validated repair without retrying or retaining provider output."""
        try:
            _validate_failed_metadata(failed, source, treatment)
            response = await self._transport.respond(
                build_reasoning_request(source, profile, treatment)
            )
            if not isinstance(response.usage, AdaptationUsage):
                raise TypeError("reasoning response usage is not normalized")
            repaired = _parse_matching_envelope(
                response.text,
                source.source_sha256,
                treatment.treatment_id,
                response.model,
                response.request_id,
                response.usage,
            )
            _validate_protected_boundaries(failed, repaired)
            turns = tuple(
                ScriptTurn(
                    turn.turn_id,
                    turn.speaker_id,
                    turn.text,
                    turn.kind,
                    turn.source_anchors,
                    turn.claim_anchors,
                )
                for turn in repaired.turns
            )
            _validate_turns(
                source, profile, turns, tuple(turn.turn_id for turn in failed.turns)
            )
        except (AdaptationError, TypeError, ValueError) as error:
            raise LiveAdaptationRepairError() from error
        return AdaptationProposal(treatment, turns)


def _validate_failed_metadata(
    failed: AdaptationEnvelope, source: SourceSnapshot, treatment: EpisodeTreatment
) -> None:
    if not isinstance(failed, AdaptationEnvelope):
        raise TypeError("failed envelope must be an AdaptationEnvelope")
    if (
        failed.source_sha256 != source.source_sha256
        or failed.treatment_id != treatment.treatment_id
    ):
        raise ValueError("failed envelope metadata does not match repair request")


def _validate_protected_boundaries(
    failed: AdaptationEnvelope, repaired: AdaptationEnvelope
) -> None:
    if len(repaired.turns) != len(failed.turns):
        raise ValueError("repair changes turn boundaries")
    for original, replacement in zip(failed.turns, repaired.turns, strict=True):
        if (
            replacement.turn_id != original.turn_id
            or replacement.speaker_id != original.speaker_id
            or replacement.kind != original.kind
            or replacement.source_anchors != original.source_anchors
            or replacement.claim_anchors != original.claim_anchors
        ):
            raise ValueError("repair changes protected turn boundaries")
