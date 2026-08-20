"""Contract coverage for the adaptation-envelope JSON schema."""

import json
from pathlib import Path


def test_adaptation_envelope_schema_freezes_the_versioned_wire_contract() -> None:
    """The schema exposes only the frozen R01 fields and vocabulary."""
    schema = json.loads(
        Path(
            "specs/003-durable-audio-production/contracts/"
            "adaptation-envelope.schema.json"
        ).read_text()
    )

    assert schema["properties"]["schema_version"] == {"const": "1.0"}
    assert set(schema["required"]) == {
        "schema_version",
        "source_sha256",
        "treatment_id",
        "model",
        "request_id",
        "turns",
        "usage",
        "estimated_cost",
    }
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["adaptedTurn"]["required"] == [
        "turn_id",
        "speaker_id",
        "kind",
        "text",
        "source_anchors",
        "claim_anchors",
    ]
    assert schema["$defs"]["adaptedTurn"]["properties"]["kind"] == {
        "enum": ["factual", "editorial"]
    }
    assert (
        schema["$defs"]["sourceAnchor"]["$comment"]
        == "The parser enforces start <= end for half-open source ranges."
    )
    assert schema["$defs"]["usage"]["additionalProperties"] == {
        "type": "integer",
        "minimum": 0,
    }
    assert "required" not in schema["$defs"]["usage"]
