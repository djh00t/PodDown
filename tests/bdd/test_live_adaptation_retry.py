"""BDD bindings for fail-closed adaptation retry policy."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.live_adaptation import LiveAdaptationError, LiveAdaptationService
from poddown.content.models import Profile, SourceAnchor, SpeakerProfile
from poddown.content.openai_reasoning import ReasoningResponse
from poddown.content.source import snapshot_source

scenarios("../features/live_adaptation_retry.feature")


class _UnsupportedTransport:
    def __init__(self):
        self.requests = []

    async def respond(self, request):
        self.requests.append(request)
        anchor = request.source_blocks[1]
        return ReasoningResponse(
            text=json.dumps(
                {
                    "schema_version": "1.0",
                    "source_sha256": request.source_sha256,
                    "treatment_id": request.treatment_id,
                    "model": "gpt-5-mini",
                    "request_id": "resp-unsupported-1",
                    "turns": [
                        {
                            "turn_id": "turn-1",
                            "speaker_id": "host",
                            "kind": "factual",
                            "text": "An unsupported invented claim.",
                            "source_anchors": [
                                {
                                    "block_id": anchor["block_id"],
                                    "start": anchor["start"],
                                    "end": anchor["end"],
                                }
                            ],
                            "claim_anchors": [
                                {
                                    "block_id": anchor["block_id"],
                                    "start": anchor["start"],
                                    "end": anchor["end"],
                                }
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 4},
                    "estimated_cost": "0.001",
                }
            ),
            model="gpt-5-mini",
            request_id="resp-unsupported-1",
            usage={"input_tokens": 3, "output_tokens": 4},
            estimated_cost=Decimal("0.001"),
        )


@given("a live adaptation service with an unsupported-claim response")
def unsupported_service(context):
    source = snapshot_source("# Topic\n\nThe system uses 44.1 kHz audio.\n")
    profile = Profile(
        profile_id="technical-dialogue",
        version="1",
        format_type="narration",
        target_minutes=10,
        speakers=(SpeakerProfile("host", "Host", "voice-host"),),
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
        format_type="narration",
        narrative_arc=("overview",),
        target_minutes=10,
        sections=("overview",),
        speaker_roles={"host": "host"},
        source_anchors=(anchor,),
        expected_turn_ids=("turn-1",),
    )
    transport = _UnsupportedTransport()
    context.values.update(
        source=source,
        profile=profile,
        treatment=treatment,
        transport=transport,
        service=LiveAdaptationService(transport),
    )


@when("I run live adaptation")
def run_unsupported(context):
    try:
        asyncio.run(
            context.values["service"].adapt(
                context.values["source"],
                context.values["profile"],
                context.values["treatment"],
            )
        )
    except LiveAdaptationError as error:
        context.values["error"] = error


@then("live adaptation fails without a repair dispatch")
def terminal_unsupported(context):
    assert context.values["error"].code == "unsupported_claim"
    assert len(context.values["transport"].requests) == 1
