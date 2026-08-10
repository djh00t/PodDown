"""Tests for deterministic capability-aware canonical-script segmentation."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace

import pytest

from poddown.content.models import ScriptTurn, ScriptVersion, SourceAnchor
from poddown.content.source import snapshot_source
from poddown.content.tokens import CriticalToken


def _script(
    source_text: str,
    turn_texts: tuple[str, ...],
    speakers: tuple[str, ...] | None = None,
) -> tuple[ScriptVersion, object, tuple[SourceAnchor, ...]]:
    source = snapshot_source(source_text)
    speaker_ids = speakers or tuple("host" for _ in turn_texts)
    anchors = tuple(
        SourceAnchor(
            block.block_id,
            block.start,
            block.end,
        )
        for block in source.blocks
    )
    turns = tuple(
        ScriptTurn(
            f"turn-{index + 1}",
            speaker_ids[index],
            text,
            "factual",
            (anchors[index],),
            (anchors[index],),
        )
        for index, text in enumerate(turn_texts)
    )
    script = ScriptVersion(
        "script-1",
        source.source_sha256,
        "profile-1",
        turns,
        hashlib.sha256(b"script-1").hexdigest(),
    )
    return script, source, anchors


def _capabilities(*, characters: int = 100, duration: float | None = None):
    from poddown.content.segmentation import SegmentationCapabilities

    return SegmentationCapabilities(characters, duration, frozenset({"host", "guest"}))


def _token(anchor: SourceAnchor, occurrence_id: str = "token-1") -> CriticalToken:
    return CriticalToken(
        occurrence_id,
        "technical_term",
        "alpha",
        (anchor.start, anchor.end),
        (0, 1),
        "alpha",
        None,
    )


def test_capabilities_are_frozen_and_reject_invalid_values():
    """Accepting an empty renderer capability set would allow unsafe requests."""
    from poddown.content.segmentation import SegmentationCapabilities

    with pytest.raises(ValueError):
        SegmentationCapabilities(0, None, frozenset({"host"}))
    with pytest.raises(ValueError):
        SegmentationCapabilities(10, 0, frozenset({"host"}))
    with pytest.raises(ValueError):
        SegmentationCapabilities(10, None, frozenset())
    with pytest.raises((AttributeError, TypeError)):
        _capabilities().max_text_characters = 1  # type: ignore[misc]


def test_segment_script_groups_complete_contiguous_turns_at_the_exact_limit():
    """Changing the boundary check would split a request that exactly fits."""
    from poddown.content.segmentation import segment_script

    script, source, anchors = _script(
        "alpha\n\nbeta\n\ngamma", ("alpha", "beta", "gamma")
    )

    segments = segment_script(script, source, _capabilities(characters=10), ())

    assert [segment.turn_ids for segment in segments] == [
        ("turn-1", "turn-2"),
        ("turn-3",),
    ]
    assert [segment.speaker_ids for segment in segments] == [("host",), ("host",)]
    assert [segment.text for segment in segments] == ["alpha\nbeta", "gamma"]
    assert tuple(turn_id for segment in segments for turn_id in segment.turn_ids) == (
        "turn-1",
        "turn-2",
        "turn-3",
    )
    assert segments[0].source_anchors == (anchors[0], anchors[1])
    assert segments[1].source_anchors == (anchors[2],)
    assert segments[0].trailing_context == "gamma"
    assert segments[1].leading_context == "alpha\nbeta"
    assert "gamma" not in segments[0].text
    assert "alpha" not in segments[1].text


def test_segment_script_assigns_tokens_by_occurrence_overlap_and_marks_difficulty():
    """Matching token strings instead of spans would lose repeated occurrences."""
    from poddown.content.segmentation import segment_script

    script, source, anchors = _script("alpha\n\nbeta", ("alpha", "beta"))
    first = _token(anchors[0], "token-1")
    second = _token(anchors[1], "token-2")

    segments = segment_script(
        script, source, _capabilities(characters=5), (first, second)
    )

    assert [segment.critical_tokens for segment in segments] == [(first,), (second,)]
    assert [segment.difficulty for segment in segments] == ["difficult", "difficult"]


def test_segment_script_rejects_unsupported_speakers_and_oversized_turns():
    """Segmentation must fail instead of sending unsupported or split turns."""
    from poddown.content.segmentation import SegmentationError, segment_script

    unsupported, source, _ = _script("alpha", ("alpha",), ("unapproved",))
    with pytest.raises(
        SegmentationError, match="unsupported_speaker"
    ) as unsupported_error:
        segment_script(
            unsupported,
            source,
            _capabilities(characters=10),
            (),
        )
    assert unsupported_error.value.code == "unsupported_speaker"

    oversized, source, _ = _script("alphabet", ("alphabet",))
    with pytest.raises(SegmentationError, match="turn_too_large") as oversized_error:
        segment_script(oversized, source, _capabilities(characters=7), ())
    assert oversized_error.value.code == "turn_too_large"


def test_segment_script_permits_anchorless_editorial_turns():
    """Catch segmentation rejecting editorial turns accepted by adaptation."""
    from poddown.content.segmentation import segment_script

    script, source, _ = _script("alpha", ("alpha",))
    editorial = replace(
        script.turns[0],
        text="Could you explain that?",
        kind="editorial",
        source_anchors=(),
        claim_anchors=(),
    )
    script = replace(script, turns=(editorial,))

    segments = segment_script(script, source, _capabilities(characters=100), ())

    assert segments[0].turn_ids == ("turn-1",)
    assert segments[0].source_anchors == ()


def test_segment_script_rejects_invalid_script_source_and_token_inputs():
    """Unordered or detached provenance cannot produce a canonical manifest."""
    from poddown.content.segmentation import SegmentationError, segment_script

    script, source, anchors = _script("alpha\n\nbeta", ("alpha", "beta"))
    reversed_turns = ScriptVersion(
        script.script_id,
        script.source_sha256,
        script.profile_id,
        tuple(reversed(script.turns)),
        script.canonical_hash,
    )
    duplicate_turns = ScriptVersion(
        script.script_id,
        script.source_sha256,
        script.profile_id,
        script.turns,
        script.canonical_hash,
    )
    object.__setattr__(duplicate_turns, "turns", (script.turns[0], script.turns[0]))
    unknown_anchor_turns = ScriptVersion(
        script.script_id,
        script.source_sha256,
        script.profile_id,
        (
            replace(
                script.turns[0],
                source_anchors=(
                    SourceAnchor("unknown-block", anchors[0].start, anchors[0].end),
                ),
            ),
            script.turns[1],
        ),
        script.canonical_hash,
    )
    detached = CriticalToken(
        "detached",
        "number",
        "1",
        (999, 1000),
        (0, 1),
        "one",
        None,
    )

    for invalid_script, invalid_tokens in (
        (reversed_turns, ()),
        (duplicate_turns, ()),
        (unknown_anchor_turns, ()),
        (script, (detached,)),
    ):
        with pytest.raises(SegmentationError, match="invalid_script") as error:
            segment_script(invalid_script, source, _capabilities(), invalid_tokens)
        assert error.value.code == "invalid_script"
    assert anchors


def test_segment_script_is_deterministic_for_identical_inputs_and_duration_limits():
    """Replacing stable boundary identifiers would break replay parity."""
    from poddown.content.segmentation import segment_script

    script, source, _ = _script("alpha\n\nbeta", ("alpha", "beta"))

    first = segment_script(script, source, _capabilities(duration=0.6), ())
    second = segment_script(script, source, _capabilities(duration=0.6), ())

    assert first == second
    assert [segment.turn_ids for segment in first] == [("turn-1",), ("turn-2",)]
    assert all(segment.segment_id.startswith("segment-") for segment in first)


def test_segment_ids_distinguish_ambiguous_turn_id_boundaries():
    """Colon-joining turn IDs would collide for different canonical boundaries."""
    from poddown.content.segmentation import segment_script

    first_script, source, _ = _script("alpha\n\nbeta", ("alpha", "beta"))
    first_turns = tuple(
        replace(turn, turn_id=turn_id)
        for turn, turn_id in zip(first_script.turns, ("a:b", "c"), strict=True)
    )
    second_turns = tuple(
        replace(turn, turn_id=turn_id)
        for turn, turn_id in zip(first_script.turns, ("a", "b:c"), strict=True)
    )
    first_script = replace(first_script, turns=first_turns)
    second_script = replace(first_script, turns=second_turns)

    first = segment_script(first_script, source, _capabilities(), ())
    second = segment_script(second_script, source, _capabilities(), ())

    assert first[0].segment_id != second[0].segment_id


@pytest.mark.parametrize(
    "source_span",
    [
        (3, 4),  # The second byte of the UTF-8 encoding of "é".
        (0, 6),  # Extends beyond the five source bytes in "café".
    ],
)
def test_segment_script_rejects_invalid_utf8_or_non_contained_token_spans(source_span):
    """Token assignment must reject malformed or non-contained UTF-8 byte spans."""
    from poddown.content.segmentation import SegmentationError, segment_script

    script, source, anchors = _script("café", ("café",))
    token = CriticalToken(
        "invalid-span",
        "technical_term",
        "café",
        source_span,
        (0, 1),
        "cafe",
        None,
    )

    with pytest.raises(SegmentationError, match="invalid_script"):
        segment_script(script, source, _capabilities(), (token,))
    assert anchors[0].end == len("café".encode())


@pytest.mark.parametrize("duration", [math.nan, math.inf, -math.inf])
def test_capabilities_reject_non_finite_duration_limits(duration):
    """Non-finite limits cannot safely bound a renderer request."""
    from poddown.content.segmentation import SegmentationCapabilities

    with pytest.raises(ValueError):
        SegmentationCapabilities(100, duration, frozenset({"host"}))


@pytest.mark.parametrize("duration", [math.nan, math.inf, -math.inf])
def test_segment_rejects_non_finite_estimated_duration(duration):
    """A segment manifest must never contain a non-finite estimated duration."""
    from poddown.content.segmentation import Segment

    _, _, anchors = _script("alpha", ("alpha",))

    with pytest.raises(ValueError):
        Segment(
            "segment-1",
            ("turn-1",),
            ("host",),
            "alpha",
            (anchors[0],),
            (),
            "",
            "",
            duration,
            "normal",
        )


def test_segment_script_legacy_mapping_returns_read_only_capability_rejection():
    """The frozen BDD mapping gets only its narrow capability-failure contract."""
    from collections.abc import Mapping

    from poddown.content.segmentation import segment_script

    result = segment_script(
        {
            "turns": [
                {
                    "turn_id": "t-001",
                    "speaker_id": "spk-engineer-a",
                    "source_block_anchor": "block-robotics-overview",
                    "text": "word " * 5000,
                }
            ],
            "renderer_text_limit": 240,
        }
    )

    assert isinstance(result, Mapping)
    assert result["accepted"] is False
    assert "capability" in str(result["error"]).lower()
    with pytest.raises(TypeError):
        result["accepted"] = True  # type: ignore[index]
    with pytest.raises(AttributeError):
        result.accepted = True  # type: ignore[attr-defined]
    assert result["accepted"] is False
    assert "capability" in str(result["error"]).lower()


def test_segment_script_legacy_mapping_rejects_non_oversized_or_malformed_input():
    """The compatibility boundary must not claim success without canonical inputs."""
    from poddown.content.segmentation import SegmentationError, segment_script

    valid_but_incomplete = {
        "turns": [
            {
                "turn_id": "t-001",
                "speaker_id": "spk-engineer-a",
                "source_block_anchor": "block-robotics-overview",
                "text": "short",
            }
        ],
        "renderer_text_limit": 240,
    }
    malformed = {"turns": [], "renderer_text_limit": 240}

    for legacy_script in (valid_but_incomplete, malformed):
        with pytest.raises(SegmentationError) as error:
            segment_script(legacy_script)
        assert error.value.code == "invalid_script"


@pytest.mark.parametrize(
    "later_turn",
    [
        {
            "turn_id": "t-001",
            "speaker_id": "spk-engineer-b",
            "source_block_anchor": "block-robotics-controls",
            "text": "later duplicate",
        },
        {
            "turn_id": "t-002",
            "speaker_id": "spk-engineer-b",
            "source_block_anchor": "block-robotics-controls",
        },
    ],
)
def test_legacy_oversized_first_turn_cannot_hide_invalid_later_turn(later_turn):
    """A capability result cannot bypass validation of later legacy turns."""
    from poddown.content.segmentation import SegmentationError, segment_script

    legacy_script = {
        "turns": [
            {
                "turn_id": "t-001",
                "speaker_id": "spk-engineer-a",
                "source_block_anchor": "block-robotics-overview",
                "text": "word " * 5000,
            },
            later_turn,
        ],
        "renderer_text_limit": 240,
    }

    with pytest.raises(SegmentationError) as error:
        segment_script(legacy_script)
    assert error.value.code == "invalid_script"
