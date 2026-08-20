"""BDD bindings for strict adaptation envelopes."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from pytest_bdd import given, scenarios, then, when

from poddown.content.adaptation_envelope import parse_adaptation_envelope
from poddown.content.models import SourceAnchor

scenarios("../features/adaptation_envelope.feature")


def _record() -> dict[str, object]:
    source_hash = hashlib.sha256(b"source").hexdigest()
    return {
        "schema_version": "1.0",
        "source_sha256": source_hash,
        "treatment_id": "treatment-1",
        "model": "gpt-5-mini",
        "request_id": "resp-1",
        "turns": [
            {
                "turn_id": "turn-1",
                "speaker_id": "host",
                "kind": "factual",
                "text": "The result is 44.1 kHz.",
                "source_anchors": [{"block_id": "b1", "start": 0, "end": 5}],
                "claim_anchors": [{"block_id": "b1", "start": 0, "end": 5}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 12},
        "estimated_cost": "0.002",
    }


@given("a valid source-bound adaptation envelope")
def valid_envelope(context):
    context.values["record"] = _record()


@given("a valid source-bound adaptation envelope with an extra field")
def extra_envelope(context):
    context.values["record"] = {**_record(), "extra": True}


@when("I parse the adaptation envelope")
def parse_envelope_step(context):
    try:
        context.values["result"] = parse_adaptation_envelope(context.values["record"])
    except ValueError as error:
        context.values["error"] = error


@then("the envelope preserves its source hash and anchors")
def envelope_preserves(context):
    result = context.values["result"]
    assert result.source_sha256 == _record()["source_sha256"]
    assert result.turns[0].source_anchors == (SourceAnchor("b1", 0, 5),)
    assert result.estimated_cost == Decimal("0.002")


@then("the adaptation envelope is rejected")
def envelope_rejected(context):
    assert isinstance(context.values.get("error"), ValueError)
