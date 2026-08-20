"""Unit coverage for live adaptation fail-closed controls."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.live_adaptation import LiveAdaptationError, LiveAdaptationService
from poddown.content.models import Profile, SpeakerProfile
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.reasoning_request import ReasoningRequest
from poddown.content.source import snapshot_source


class _Transport:
    def __init__(self, response: ReasoningResponse):
        self.response = response
        self.requests: list[ReasoningRequest] = []

    async def respond(self, request: ReasoningRequest) -> ReasoningResponse:
        self.requests.append(request)
        return self.response


def test_repair_budget_and_failure_class_are_terminal() -> None:
    transport = _Transport(
        ReasoningResponse("{}", "gpt-5-mini", "resp", {"input_tokens": 1}, Decimal("0"))
    )
    service = LiveAdaptationService(transport)
    source = snapshot_source("# T\n\nSource.\n")
    profile = Profile(
        profile_id="default",
        version="1",
        format_type="narration",
        target_minutes=1,
        speakers=(SpeakerProfile("host", "Host", "voice"),),
        style={},
        audio={},
        quality={},
        document_overridable=frozenset(),
    )
    treatment = EpisodeTreatment(
        treatment_id="t",
        format_type="narration",
        narrative_arc=("a",),
        target_minutes=1,
        sections=("a",),
        speaker_roles={"host": "host"},
        source_anchors=(
            __import__(
                "poddown.content.models", fromlist=["SourceAnchor"]
            ).SourceAnchor(
                source.blocks[1].block_id, source.blocks[1].start, source.blocks[1].end
            ),
        ),
        expected_turn_ids=("turn",),
    )
    script = __import__(
        "poddown.content.models", fromlist=["ScriptVersion"]
    ).ScriptVersion("script", source.source_sha256, profile.profile_id, (), "a" * 64)
    with pytest.raises(LiveAdaptationError, match="repair_not_permitted"):
        asyncio.run(
            service.repair(
                source, profile, treatment, script, "turn", "unsupported_claim"
            )
        )
    with pytest.raises(LiveAdaptationError, match="repair_budget_exhausted"):
        asyncio.run(
            service.repair(
                source,
                profile,
                treatment,
                script,
                "turn",
                "dialogue_quality",
                repair_count=1,
            )
        )
    assert transport.requests == []
