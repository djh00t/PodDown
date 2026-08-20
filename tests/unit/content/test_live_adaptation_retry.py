"""Unit coverage for one fail-closed live adaptation retry."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.adaptation_envelope import AdaptationUsage
from poddown.content.models import Profile, SourceAnchor, SourceSnapshot, SpeakerProfile
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.source import snapshot_source


class FakeTransport:
    """Offline R03 double with an explicit ordered response queue."""

    def __init__(self, *responses: ReasoningResponse) -> None:
        self._responses = list(responses)
        self.requests: list[object] = []

    async def respond(self, request: object) -> ReasoningResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def _inputs() -> tuple[SourceSnapshot, Profile, EpisodeTreatment, dict[str, object]]:
    source = snapshot_source("# Context\n\nThe controller is not silent.\n")
    block = source.blocks[1]
    profile = Profile(
        "profile-001",
        "1.0",
        "narration",
        5,
        (SpeakerProfile("host", "Host", "voice-host"),),
        {},
        {},
        {},
        frozenset(),
    )
    treatment = EpisodeTreatment(
        "treatment-001",
        "narration",
        ("context",),
        5,
        ("context",),
        {"host": "narrator"},
        (SourceAnchor(block.block_id, block.start, block.end),),
        ("turn-001",),
    )
    return (
        source,
        profile,
        treatment,
        {
            "schema_version": "1.0",
            "source_sha256": source.source_sha256,
            "treatment_id": treatment.treatment_id,
            "model": "gpt-4.1-mini",
            "request_id": "resp-001",
            "turns": [
                {
                    "turn_id": "turn-001",
                    "speaker_id": "host",
                    "kind": "factual",
                    "text": "The controller is silent.",
                    "source_anchors": [
                        {
                            "block_id": block.block_id,
                            "start": block.start,
                            "end": block.end,
                        }
                    ],
                    "claim_anchors": [
                        {
                            "block_id": block.block_id,
                            "start": block.start,
                            "end": block.end,
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 8, "output_tokens": 5},
            "estimated_cost": "0.00125",
        },
    )


def _response(record: dict[str, object], request_id: str) -> ReasoningResponse:
    return ReasoningResponse(
        text=json.dumps(record),
        model="gpt-4.1-mini",
        request_id=request_id,
        usage=AdaptationUsage(values=dict(record["usage"])),
    )


def _repaired(record: dict[str, object]) -> dict[str, object]:
    repaired = json.loads(json.dumps(record))
    repaired["request_id"] = "resp-002"
    repaired["usage"] = {"input_tokens": 10, "output_tokens": 6}
    repaired["turns"][0]["text"] = "The controller is not silent."
    return repaired


def _dialogue_inputs() -> tuple[
    SourceSnapshot, Profile, EpisodeTreatment, dict[str, object]
]:
    source = snapshot_source(
        "# Context\n\nThe controller is not silent.\n\n"
        "However, the system requires review.\n"
    )
    controller = source.blocks[1]
    review = source.blocks[2]
    profile = Profile(
        "profile-001",
        "1.0",
        "dialogue",
        5,
        (
            SpeakerProfile("host", "Host", "voice-host"),
            SpeakerProfile("guest", "Guest", "voice-guest"),
        ),
        {},
        {},
        {},
        frozenset(),
    )
    treatment = EpisodeTreatment(
        "treatment-001",
        "dialogue",
        ("context",),
        5,
        ("context",),
        {"host": "host", "guest": "guest"},
        (
            SourceAnchor(controller.block_id, controller.start, controller.end),
            SourceAnchor(review.block_id, review.start, review.end),
        ),
        ("turn-001", "turn-002"),
    )
    controller_anchor = {
        "block_id": controller.block_id,
        "start": controller.start,
        "end": controller.end,
    }
    review_anchor = {
        "block_id": review.block_id,
        "start": review.start,
        "end": review.end,
    }
    return (
        source,
        profile,
        treatment,
        {
            "schema_version": "1.0",
            "source_sha256": source.source_sha256,
            "treatment_id": treatment.treatment_id,
            "model": "gpt-4.1-mini",
            "request_id": "resp-001",
            "turns": [
                {
                    "turn_id": "turn-001",
                    "speaker_id": "host",
                    "kind": "factual",
                    "text": "The controller is not silent.",
                    "source_anchors": [controller_anchor],
                    "claim_anchors": [controller_anchor],
                },
                {
                    "turn_id": "turn-002",
                    "speaker_id": "guest",
                    "kind": "factual",
                    "text": "The system requires review.",
                    "source_anchors": [review_anchor],
                    "claim_anchors": [review_anchor],
                },
            ],
            "usage": {"input_tokens": 8, "output_tokens": 5},
            "estimated_cost": "0.00125",
        },
    )


def _dialogue_repaired(record: dict[str, object]) -> dict[str, object]:
    repaired = json.loads(json.dumps(record))
    repaired["request_id"] = "resp-002"
    repaired["usage"] = {"input_tokens": 10, "output_tokens": 6}
    repaired["turns"][1]["text"] = "However, the system requires review."
    return repaired


def test_retry_repairs_one_semantic_failure_and_reports_safe_reason() -> None:
    """Without R06, an approved dialogue repair cannot use R05 once."""
    from poddown.content.live_adaptation_retry import LiveAdaptationRetryPort

    source, profile, treatment, failed = _dialogue_inputs()
    repaired = _dialogue_repaired(failed)
    result = asyncio.run(
        LiveAdaptationRetryPort(
            FakeTransport(
                _response(failed, "resp-001"), _response(repaired, "resp-002")
            )
        ).adapt(source, profile, treatment)
    )

    assert result.proposal.turns[1].text == "However, the system requires review."
    assert result.proposal.turns[1].turn_id == "turn-002"
    assert result.proposal.turns[1].speaker_id == "guest"
    assert result.proposal.turns[1].kind == "factual"
    assert (
        result.proposal.turns[1].source_anchors
        == result.proposal.turns[1].claim_anchors
    )
    assert result.provider_call_count == 2
    assert result.retry_reason == "dialogue_quality"


def test_retry_stops_after_repair_failure_with_safe_structured_error() -> None:
    """A second semantic failure must not produce a third provider request."""
    from poddown.content.live_adaptation_retry import (
        LiveAdaptationRetryError,
        LiveAdaptationRetryPort,
    )

    source, profile, treatment, failed = _dialogue_inputs()
    transport = FakeTransport(
        _response(failed, "resp-001"), _response(failed, "resp-001")
    )

    with pytest.raises(LiveAdaptationRetryError) as error:
        asyncio.run(
            LiveAdaptationRetryPort(transport).adapt(source, profile, treatment)
        )

    assert error.value.code == "repair_rejected"
    assert error.value.provider_call_count == 2
    assert error.value.retry_reason == "dialogue_quality"
    assert str(error.value) == "live adaptation retry rejected"
    assert "controller" not in str(error.value)
    assert len(transport.requests) == 2


def test_retry_rejects_malformed_initial_metadata_without_rewriting_or_repair() -> None:
    """Malformed initial envelopes must remain terminal rather than being repaired."""
    from poddown.content.live_adaptation_retry import (
        LiveAdaptationRetryError,
        LiveAdaptationRetryPort,
    )

    source, profile, treatment, failed = _inputs()
    failed["source_sha256"] = "a" * 64
    transport = FakeTransport(_response(failed, "resp-001"))

    with pytest.raises(LiveAdaptationRetryError) as error:
        asyncio.run(
            LiveAdaptationRetryPort(transport).adapt(source, profile, treatment)
        )

    assert error.value.code == "initial_rejected"
    assert error.value.provider_call_count == 1
    assert error.value.retry_reason is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda record: record["turns"][0].update(text="The controller is silent."),
            id="unsupported_claim",
        ),
        pytest.param(
            lambda record: record["turns"][0].update(speaker_id="unapproved"),
            id="invalid_speaker",
        ),
        pytest.param(
            lambda record: record["turns"][0].update(claim_anchors=[]),
            id="missing_anchor",
        ),
    ],
)
def test_retry_does_not_repair_terminal_r04_validation_failures(
    change: Callable[[dict[str, object]], None],
) -> None:
    """Only dialogue quality may use repair; other R04 failures stay terminal."""
    from poddown.content.live_adaptation_retry import (
        LiveAdaptationRetryError,
        LiveAdaptationRetryPort,
    )

    source, profile, treatment, failed = _inputs()
    change(failed)
    transport = FakeTransport(_response(failed, "resp-001"))

    with pytest.raises(LiveAdaptationRetryError) as error:
        asyncio.run(
            LiveAdaptationRetryPort(transport).adapt(source, profile, treatment)
        )

    assert error.value.code == "initial_rejected"
    assert error.value.provider_call_count == 1
    assert error.value.retry_reason is None
    assert str(error.value) == "live adaptation retry rejected"
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "invalid_treatment",
    [
        pytest.param(
            lambda treatment: replace(treatment, target_minutes=6), id="duration"
        ),
        pytest.param(
            lambda treatment: replace(
                treatment, speaker_roles={"unapproved": "narrator"}
            ),
            id="policy_permission",
        ),
    ],
)
def test_retry_rejects_treatment_authority_failures_before_provider_calls(
    invalid_treatment: Callable[[EpisodeTreatment], EpisodeTreatment],
) -> None:
    """Profile-policy failures must not reach either adaptation or repair."""
    from poddown.content.live_adaptation_retry import (
        LiveAdaptationRetryError,
        LiveAdaptationRetryPort,
    )

    source, profile, treatment, failed = _inputs()
    transport = FakeTransport(_response(failed, "resp-001"))

    with pytest.raises(LiveAdaptationRetryError) as error:
        asyncio.run(
            LiveAdaptationRetryPort(transport).adapt(
                source, profile, invalid_treatment(treatment)
            )
        )

    assert error.value.code == "initial_rejected"
    assert error.value.provider_call_count == 0
    assert error.value.retry_reason is None
    assert str(error.value) == "live adaptation retry rejected"
    assert transport.requests == []


def test_retry_accepts_a_deterministic_local_proposal_without_provider_calls() -> None:
    """The offline path must validate locally without invoking R03."""
    from poddown.content.adaptation import AdaptationProposal
    from poddown.content.live_adaptation_retry import LiveAdaptationRetryPort
    from poddown.content.models import ScriptTurn

    source, profile, treatment, _ = _inputs()
    block = source.blocks[1]
    proposal = AdaptationProposal(
        treatment,
        (
            ScriptTurn(
                "turn-001",
                "host",
                "The controller is not silent.",
                "factual",
                (SourceAnchor(block.block_id, block.start, block.end),),
                (SourceAnchor(block.block_id, block.start, block.end),),
            ),
        ),
    )

    result = LiveAdaptationRetryPort.accept_local(source, profile, proposal)

    assert result.proposal == proposal
    assert result.provider_call_count == 0
    assert result.retry_reason is None
