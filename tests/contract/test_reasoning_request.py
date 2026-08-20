"""Contract coverage for the reasoning-request transport boundary."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from poddown.content.adaptation import EpisodeTreatment
from poddown.content.models import Profile, SourceAnchor, SpeakerProfile
from poddown.content.source import snapshot_source


def _schema() -> dict[str, object]:
    """Load the versioned closed wire contract used by R03."""
    return json.loads(
        Path(
            "specs/003-durable-audio-production/contracts/reasoning-request.schema.json"
        ).read_text()
    )


def _record() -> dict[str, object]:
    """Build one valid record through the public R02 boundary."""
    from poddown.content.reasoning_request import build_reasoning_request

    source = snapshot_source("# Context\n\nThe rate is 5%.\n")
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
    )
    return build_reasoning_request(source, profile, treatment).to_record()


def test_reasoning_request_exposes_only_json_transport_primitives() -> None:
    """R03 can inject this record without provider SDK types or credentials."""
    record = _record()

    assert json.loads(json.dumps(record, allow_nan=False, sort_keys=True)) == record
    assert set(record) == {
        "schema_version",
        "source_sha256",
        "treatment_id",
        "profile",
        "treatment",
        "source_anchors",
    }


def test_reasoning_request_record_validates_against_the_closed_wire_schema() -> None:
    """R03 receives a versioned record that validates without provider access."""
    record = _record()
    schema = _schema()

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(record)


def test_reasoning_request_schema_rejects_an_unknown_wire_field() -> None:
    """Unknown fields must not be smuggled across the R02 to R03 boundary."""
    record = _record()
    record["unexpected"] = "value"

    with pytest.raises(ValidationError):
        Draft202012Validator(_schema()).validate(record)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record["source_anchors"][0].update(start=10, end=9),  # type: ignore[index,union-attr]
        lambda record: record["source_anchors"][0]["critical_tokens"][0].update(  # type: ignore[index,union-attr]
            start=10, end=9
        ),
    ],
)
def test_reasoning_request_boundary_rejects_reversed_spans(mutate) -> None:
    """Wire records cannot bypass the domain's ordered source-span invariant."""
    from poddown.content.reasoning_request import validate_reasoning_request_record

    record = _record()
    mutate(record)

    with pytest.raises(ValueError, match="start must not exceed end"):
        validate_reasoning_request_record(record)
