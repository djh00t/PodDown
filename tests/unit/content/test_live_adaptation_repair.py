"""Unit coverage for one bounded live adaptation-envelope repair."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.adaptation_envelope import (
    AdaptationUsage,
    parse_adaptation_envelope,
)
from poddown.content.models import Profile, SourceAnchor, SourceSnapshot, SpeakerProfile
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.source import snapshot_source


class FakeTransport:
    """Deterministic R03 double that permits exactly one repair request."""

    def __init__(self, response: ReasoningResponse) -> None:
        self._response = response
        self.requests: list[object] = []

    async def respond(self, request: object) -> ReasoningResponse:
        self.requests.append(request)
        return self._response


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
            "request_id": "resp-failed",
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


def _response(record: dict[str, object]) -> ReasoningResponse:
    return ReasoningResponse(
        text=json.dumps(record),
        model="gpt-4.1-mini",
        request_id="resp-repaired",
        usage=AdaptationUsage(values={"input_tokens": 10, "output_tokens": 6}),
    )


def _repaired_record(failed: dict[str, object]) -> dict[str, object]:
    record = json.loads(json.dumps(failed))
    record["request_id"] = "resp-repaired"
    record["usage"] = {"input_tokens": 10, "output_tokens": 6}
    record["turns"][0]["text"] = "The controller is not silent."
    return record


def test_repair_failed_envelope_rewrites_one_invalid_claim_with_one_r03_call() -> None:
    """Skipping bounded repair would leave a semantically invalid envelope unusable."""
    from poddown.content.live_adaptation_repair import LiveAdaptationRepairPort

    source, profile, treatment, failed_record = _inputs()
    transport = FakeTransport(_response(_repaired_record(failed_record)))

    proposal = asyncio.run(
        LiveAdaptationRepairPort(transport).repair_failed_envelope(
            source, profile, treatment, parse_adaptation_envelope(failed_record)
        )
    )

    assert proposal.turns[0].text == "The controller is not silent."
    assert proposal.turns[0].turn_id == "turn-001"
    assert proposal.turns[0].speaker_id == "host"
    assert proposal.turns[0].source_anchors == (
        SourceAnchor(source.blocks[1].block_id, 11, 40),
    )
    assert proposal.turns[0].claim_anchors == proposal.turns[0].source_anchors
    assert len(transport.requests) == 1
    assert not hasattr(proposal, "response")


@pytest.mark.parametrize(
    "change",
    [
        lambda record: record["turns"][0].update(turn_id="turn-other"),
        lambda record: record["turns"][0].update(speaker_id="other-speaker"),
        lambda record: record["turns"][0].update(source_anchors=[]),
        lambda record: record["turns"][0].update(claim_anchors=[]),
        lambda record: record["turns"][0].update(text="The controller is silent."),
    ],
)
def test_repair_failed_envelope_rejects_changed_boundaries_or_unsupported_claims(
    change: Callable[[dict[str, object]], None],
) -> None:
    """Removing protected-boundary checks would admit altered identities or claims."""
    from poddown.content.live_adaptation_repair import (
        LiveAdaptationRepairError,
        LiveAdaptationRepairPort,
    )

    source, profile, treatment, failed_record = _inputs()
    repaired = _repaired_record(failed_record)
    change(repaired)

    with pytest.raises(LiveAdaptationRepairError) as error:
        asyncio.run(
            LiveAdaptationRepairPort(
                FakeTransport(_response(repaired))
            ).repair_failed_envelope(
                source, profile, treatment, parse_adaptation_envelope(failed_record)
            )
        )

    assert str(error.value) == "live adaptation repair rejected"
    assert "controller" not in str(error.value)


def test_repair_failed_envelope_strips_malformed_provider_payload_from_errors() -> None:
    """Leaking failed provider content would expose untrusted transport payloads."""
    from poddown.content.live_adaptation_repair import (
        LiveAdaptationRepairError,
        LiveAdaptationRepairPort,
    )

    source, profile, treatment, failed_record = _inputs()
    response = ReasoningResponse(
        text='{"credential":"do-not-leak"}',
        model="gpt-4.1-mini",
        request_id="resp-repaired",
        usage=AdaptationUsage(values={"input_tokens": 10, "output_tokens": 6}),
    )

    with pytest.raises(LiveAdaptationRepairError) as error:
        asyncio.run(
            LiveAdaptationRepairPort(FakeTransport(response)).repair_failed_envelope(
                source, profile, treatment, parse_adaptation_envelope(failed_record)
            )
        )

    assert str(error.value) == "live adaptation repair rejected"
    assert "do-not-leak" not in str(error.value)


@pytest.mark.parametrize(
    "field, value",
    [("source_sha256", "a" * 64), ("treatment_id", "other-treatment")],
)
def test_repair_failed_envelope_rejects_mismatched_source_or_treatment_metadata(
    field: str, value: str
) -> None:
    """Ignoring failed metadata could repair a different source or treatment."""
    from poddown.content.live_adaptation_repair import (
        LiveAdaptationRepairError,
        LiveAdaptationRepairPort,
    )

    source, profile, treatment, failed_record = _inputs()
    failed_record[field] = value
    transport = FakeTransport(_response(_repaired_record(failed_record)))

    with pytest.raises(LiveAdaptationRepairError) as error:
        asyncio.run(
            LiveAdaptationRepairPort(transport).repair_failed_envelope(
                source, profile, treatment, parse_adaptation_envelope(failed_record)
            )
        )

    assert str(error.value) == "live adaptation repair rejected"
    assert transport.requests == []
