"""Unit coverage for the immutable adaptation-envelope parser."""

from decimal import Decimal

import pytest

from poddown.content.adaptation_envelope import (
    AdaptationEnvelope,
    AdaptationUsage,
    AdaptedTurn,
    parse_adaptation_envelope,
)
from poddown.content.models import SourceAnchor


def _record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "source_sha256": "a" * 64,
        "treatment_id": "treatment-001",
        "model": "gpt-5",
        "request_id": "req-001",
        "turns": [
            {
                "turn_id": "turn-001",
                "speaker_id": "host",
                "kind": "factual",
                "text": "The source is preserved.",
                "source_anchors": [{"block_id": "block-001", "start": 4, "end": 27}],
                "claim_anchors": [{"block_id": "block-001", "start": 4, "end": 10}],
            }
        ],
        "usage": {"input_tokens": 12, "output_tokens": 34, "cached_tokens": 5},
        "estimated_cost": "0.00125",
    }


def test_parses_a_lossless_immutable_adaptation_envelope() -> None:
    """The parser preserves the wire anchors and canonical Decimal cost."""
    envelope = parse_adaptation_envelope(_record())

    assert envelope == AdaptationEnvelope(
        source_sha256="a" * 64,
        treatment_id="treatment-001",
        model="gpt-5",
        request_id="req-001",
        turns=(
            AdaptedTurn(
                turn_id="turn-001",
                speaker_id="host",
                kind="factual",
                text="The source is preserved.",
                source_anchors=(SourceAnchor("block-001", 4, 27),),
                claim_anchors=(SourceAnchor("block-001", 4, 10),),
            ),
        ),
        usage=AdaptationUsage(
            values={"input_tokens": 12, "output_tokens": 34, "cached_tokens": 5}
        ),
        estimated_cost=Decimal("0.00125"),
    )
    assert envelope.to_record() == _record()
    with pytest.raises(AttributeError):
        envelope.model = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda record: record.update(unexpected="value"), "fields"),
        (lambda record: record.update(treatment_id=" "), "treatment_id"),
        (lambda record: record.update(source_sha256="A" * 64), "source_sha256"),
        (
            lambda record: record["usage"].update(input_tokens=-1),  # type: ignore[index,union-attr]
            "input_tokens",
        ),
        (
            lambda record: record["usage"].update(output_tokens=True),  # type: ignore[index,union-attr]
            "output_tokens",
        ),
        (lambda record: record.update(estimated_cost="-0.01"), "estimated_cost"),
        (
            lambda record: record["turns"][0].pop("claim_anchors"),  # type: ignore[index,union-attr]
            "fields",
        ),
        (
            lambda record: record["turns"][0].update(kind="summary"),  # type: ignore[index,union-attr]
            "kind",
        ),
    ],
)
def test_rejects_malformed_adaptation_envelopes(mutate, message: str) -> None:
    """Unknown, invalid, and incomplete provider values fail closed."""
    record = _record()
    mutate(record)

    with pytest.raises(ValueError, match=message):
        parse_adaptation_envelope(record)


def test_rejects_a_direct_model_cost_that_cannot_be_serialized_to_the_schema() -> None:
    """A negative Decimal zero must not escape as an invalid wire cost."""
    with pytest.raises(ValueError, match="estimated_cost"):
        AdaptationEnvelope(
            source_sha256="a" * 64,
            treatment_id="treatment-001",
            model="gpt-5",
            request_id="req-001",
            turns=(),
            usage=AdaptationUsage(values={"input_tokens": 0, "output_tokens": 0}),
            estimated_cost=Decimal("-0"),
        )


def test_usage_accepts_provider_specific_non_negative_counters() -> None:
    """Provider-specific counters remain lossless under the generic contract."""
    usage = AdaptationUsage(values={"cached_tokens": 7, "reasoning_tokens": 3})

    assert usage.to_record() == {"cached_tokens": 7, "reasoning_tokens": 3}
