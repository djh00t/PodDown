"""BDD bindings for bounded adaptation repair."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.live_adaptation import LiveAdaptationService
from poddown.content.models import (
    Profile,
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    SpeakerProfile,
)
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.source import snapshot_source

scenarios("../features/live_adaptation_repair.feature")


class _RepairTransport:
    async def respond(self, request):
        source_hash = request.source_sha256
        return ReasoningResponse(
            text=json.dumps(
                {
                    "schema_version": "1.0",
                    "source_sha256": source_hash,
                    "treatment_id": request.treatment_id,
                    "model": "gpt-5-mini",
                    "request_id": "resp-repair-1",
                    "turns": [
                        {
                            "turn_id": request.repair_turn_id,
                            "speaker_id": "guest",
                            "kind": "editorial",
                            "text": "I disagree.",
                            "source_anchors": [],
                            "claim_anchors": [],
                        }
                    ],
                    "usage": {"input_tokens": 4, "output_tokens": 5},
                    "estimated_cost": "0.001",
                }
            ),
            model="gpt-5-mini",
            request_id="resp-repair-1",
            usage={"input_tokens": 4, "output_tokens": 5},
            estimated_cost=Decimal("0.001"),
        )


def _context():
    source = snapshot_source("# Topic\n\nThe system uses 44.1 kHz audio.\n")
    profile = Profile(
        profile_id="technical-dialogue",
        version="1",
        format_type="dialogue",
        target_minutes=10,
        speakers=(
            SpeakerProfile("host", "Host", "voice-host"),
            SpeakerProfile("guest", "Guest", "voice-guest"),
        ),
        style={},
        audio={},
        quality={},
        document_overridable=frozenset(),
    )
    anchor = SourceAnchor(
        source.blocks[1].block_id, source.blocks[1].start, source.blocks[1].end
    )
    treatment = EpisodeTreatment(
        treatment_id="treatment-1",
        format_type="dialogue",
        narrative_arc=("overview",),
        target_minutes=10,
        sections=("overview",),
        speaker_roles={"host": "host", "guest": "guest"},
        source_anchors=(anchor,),
        expected_turn_ids=("turn-1", "turn-2"),
    )
    turns = (
        ScriptTurn(
            "turn-1",
            "host",
            "The system uses 44.1 kHz audio.",
            "factual",
            (anchor,),
            (anchor,),
        ),
        ScriptTurn("turn-2", "guest", "I disagree.", "editorial", (), ()),
    )
    script = ScriptVersion(
        "script-old", source.source_sha256, profile.profile_id, turns, "a" * 64
    )
    return source, profile, treatment, script


@given("a live adaptation service with a valid repair response")
def repair_service(context):
    source, profile, treatment, script = _context()
    context.values.update(
        source=source,
        profile=profile,
        treatment=treatment,
        script=script,
        service=LiveAdaptationService(_RepairTransport()),
    )


@when("I repair one dialogue-quality turn")
def run_repair(context):
    context.values["result"] = asyncio.run(
        context.values["service"].repair(
            context.values["source"],
            context.values["profile"],
            context.values["treatment"],
            context.values["script"],
            "turn-2",
            "dialogue_quality",
        )
    )


@then("the repaired script preserves the protected turn identity")
def repaired_identity(context):
    original = context.values["script"].turns[1]
    repaired = context.values["result"].turns[1]
    assert repaired.turn_id == original.turn_id
    assert repaired.speaker_id == original.speaker_id
    assert repaired.kind == original.kind
    assert repaired.source_anchors == original.source_anchors
    assert repaired.claim_anchors == original.claim_anchors
    assert repaired.text == "I disagree."
