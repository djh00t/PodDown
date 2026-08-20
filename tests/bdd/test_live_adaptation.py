"""BDD bindings for injected live adaptation."""

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
from poddown.providers.contracts import ProviderEvidence

scenarios("../features/live_adaptation.feature")


class _Transport:
    def __init__(self, body: dict[str, object], usage: dict[str, int] | None = None):
        self.body = body
        self.usage = usage or {"input_tokens": 10, "output_tokens": 20}
        self.requests = []

    async def respond(self, request):
        self.requests.append(request)
        return ReasoningResponse(
            text=json.dumps(self.body),
            model="gpt-5-mini",
            request_id="resp-adapt-1",
            usage=self.usage,
            estimated_cost=Decimal("0.002"),
        )


def _context(usage: dict[str, int] | None = None):
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
    body = {
        "schema_version": "1.0",
        "source_sha256": source.source_sha256,
        "treatment_id": treatment.treatment_id,
        "model": "gpt-5-mini",
        "request_id": "resp-adapt-1",
        "turns": [
            {
                "turn_id": "turn-1",
                "speaker_id": "host",
                "kind": "factual",
                "text": "The system uses 44.1 kHz audio.",
                "source_anchors": [
                    {
                        "block_id": anchor.block_id,
                        "start": anchor.start,
                        "end": anchor.end,
                    }
                ],
                "claim_anchors": [
                    {
                        "block_id": anchor.block_id,
                        "start": anchor.start,
                        "end": anchor.end,
                    }
                ],
            },
            {
                "turn_id": "turn-2",
                "speaker_id": "guest",
                "kind": "editorial",
                "text": "I disagree.",
                "source_anchors": [],
                "claim_anchors": [],
            },
        ],
        "usage": usage or {"input_tokens": 10, "output_tokens": 20},
        "estimated_cost": "0.002",
    }
    return source, profile, treatment, _Transport(body, usage)


@given("a live adaptation service with a valid provider response")
def live_service(context):
    source, profile, treatment, transport = _context()
    evidence: list[ProviderEvidence] = []
    context.values.update(
        source=source,
        profile=profile,
        treatment=treatment,
        transport=transport,
        service=LiveAdaptationService(transport, evidence_recorder=evidence.append),
        evidence=evidence,
    )


@given("a live adaptation service with non-normalized provider usage")
def non_normalized_live_service(context):
    source, profile, treatment, transport = _context(
        {"input_tokens": 10, "output_tokens": 20, "cached_tokens": 1}
    )
    evidence: list[ProviderEvidence] = []
    context.values.update(
        source=source,
        profile=profile,
        treatment=treatment,
        transport=transport,
        service=LiveAdaptationService(transport, evidence_recorder=evidence.append),
    )


@when("I run live adaptation")
def run_live_adaptation(context):
    try:
        context.values["script"] = asyncio.run(
            context.values["service"].adapt(
                context.values["source"],
                context.values["profile"],
                context.values["treatment"],
            )
        )
    except LiveAdaptationError as error:
        context.values["error"] = error


@then("the adapted script preserves the source hash")
def adapted_source_hash(context):
    assert (
        context.values["script"].source_sha256 == context.values["source"].source_sha256
    )


@then("one reasoning evidence record is produced")
def reasoning_evidence(context):
    assert len(context.values["evidence"]) == 1
    assert context.values["evidence"][0].operation == "adapt"


@then("live adaptation fails with a provider metering error")
def provider_metering_error(context):
    assert context.values["error"].code == "provider_metering_failed"
