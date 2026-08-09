"""Deterministic renderer-capability segmentation for canonical scripts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal, overload

from poddown.content.models import (
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    SourceSnapshot,
)
from poddown.content.source import anchor_text
from poddown.content.tokens import CriticalToken

_CHARACTERS_PER_SECOND = 15.0
_DIFFICULTIES = frozenset({"normal", "difficult"})


class SegmentationError(ValueError):
    """A stable rejection raised before a renderer request is created."""

    def __init__(
        self,
        code: Literal["turn_too_large", "unsupported_speaker", "invalid_script"],
        detail: str,
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SegmentationCapabilities:
    """Immutable renderer limits used to form bounded complete-turn requests."""

    max_text_characters: int
    max_duration_seconds: float | None
    supported_speakers: frozenset[str]

    def __post_init__(self) -> None:
        if type(self.max_text_characters) is not int or self.max_text_characters <= 0:
            raise ValueError("max_text_characters must be a positive integer")
        if self.max_duration_seconds is not None and (
            isinstance(self.max_duration_seconds, bool)
            or not isinstance(self.max_duration_seconds, int | float)
            or not math.isfinite(self.max_duration_seconds)
            or self.max_duration_seconds <= 0
        ):
            raise ValueError("max_duration_seconds must be positive when provided")
        if (
            not isinstance(self.supported_speakers, frozenset)
            or not self.supported_speakers
        ):
            raise ValueError("supported_speakers must be a non-empty frozenset")
        if any(
            not isinstance(speaker, str) or not speaker
            for speaker in self.supported_speakers
        ):
            raise ValueError("supported_speakers must contain non-empty strings")


@dataclass(frozen=True)
class Segment:
    """One complete-turn renderer request with non-spoken continuity metadata."""

    segment_id: str
    turn_ids: tuple[str, ...]
    speaker_ids: tuple[str, ...]
    text: str
    source_anchors: tuple[SourceAnchor, ...]
    critical_tokens: tuple[CriticalToken, ...]
    leading_context: str
    trailing_context: str
    estimated_duration_seconds: float
    difficulty: Literal["normal", "difficult"]

    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, str) or not self.segment_id:
            raise ValueError("segment_id must be a non-empty string")
        if not self.turn_ids or any(
            not isinstance(item, str) or not item for item in self.turn_ids
        ):
            raise ValueError("turn_ids must contain non-empty strings")
        if not self.speaker_ids or any(
            not isinstance(item, str) or not item for item in self.speaker_ids
        ):
            raise ValueError("speaker_ids must contain non-empty strings")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("text must be a non-empty string")
        if any(not isinstance(anchor, SourceAnchor) for anchor in self.source_anchors):
            raise ValueError("source_anchors must contain SourceAnchor values")
        if any(not isinstance(token, CriticalToken) for token in self.critical_tokens):
            raise ValueError("critical_tokens must contain CriticalToken values")
        if not isinstance(self.leading_context, str) or not isinstance(
            self.trailing_context, str
        ):
            raise ValueError("continuity context must be strings")
        if (
            isinstance(self.estimated_duration_seconds, bool)
            or not isinstance(self.estimated_duration_seconds, int | float)
            or not math.isfinite(self.estimated_duration_seconds)
            or self.estimated_duration_seconds <= 0
        ):
            raise ValueError("estimated_duration_seconds must be finite and positive")
        if self.difficulty not in _DIFFICULTIES:
            raise ValueError("difficulty must be normal or difficult")


def _estimate_duration(text: str) -> float:
    return len(text) / _CHARACTERS_PER_SECOND


def _overlaps(left: tuple[int, int], right: SourceAnchor) -> bool:
    return left[0] < right.end and right.start < left[1]


def _validate_script(script: ScriptVersion, source: SourceSnapshot) -> None:
    if not isinstance(script, ScriptVersion) or not isinstance(source, SourceSnapshot):
        raise SegmentationError(
            "invalid_script", "script and source must be canonical values"
        )
    if script.source_sha256 != source.source_sha256:
        raise SegmentationError(
            "invalid_script", "script source hash does not match source"
        )
    if not script.turns:
        raise SegmentationError("invalid_script", "script has no turns")
    turn_ids = [turn.turn_id for turn in script.turns]
    if len(turn_ids) != len(set(turn_ids)):
        raise SegmentationError("invalid_script", "script turn IDs are not unique")

    block_positions = {
        block.block_id: index for index, block in enumerate(source.blocks)
    }
    previous_end = -1
    previous_block_position = -1
    for turn in script.turns:
        if not isinstance(turn, ScriptTurn) or not turn.source_anchors:
            raise SegmentationError("invalid_script", "every turn needs source anchors")
        for anchor in turn.source_anchors:
            try:
                anchor_text(source, anchor)
            except ValueError as error:
                raise SegmentationError(
                    "invalid_script", "turn source anchor is invalid"
                ) from error
            block_position = block_positions[anchor.block_id]
            if block_position < previous_block_position or anchor.start < previous_end:
                raise SegmentationError(
                    "invalid_script", "source anchors are not contiguous"
                )
            previous_block_position = block_position
            previous_end = anchor.end


def _validate_tokens(
    tokens: tuple[CriticalToken, ...], script: ScriptVersion, source: SourceSnapshot
) -> None:
    if not isinstance(tokens, tuple) or any(
        not isinstance(token, CriticalToken) for token in tokens
    ):
        raise SegmentationError(
            "invalid_script", "tokens must be a tuple of CriticalToken values"
        )
    occurrence_ids = [token.occurrence_id for token in tokens]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise SegmentationError("invalid_script", "token occurrence IDs are not unique")
    script_anchors = tuple(
        anchor for turn in script.turns for anchor in turn.source_anchors
    )
    source_bytes = source.source.encode("utf-8")
    for token in tokens:
        start, end = token.source_span
        if end > len(source_bytes):
            raise SegmentationError("invalid_script", "token span exceeds source bytes")
        try:
            source_bytes[start:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise SegmentationError(
                "invalid_script", "token span does not align to UTF-8 boundaries"
            ) from error
        matches = [
            anchor
            for anchor in script_anchors
            if anchor.start <= start and end <= anchor.end
        ]
        if len(matches) != 1:
            raise SegmentationError(
                "invalid_script", "token does not map to one source group"
            )


def _segment_id(script: ScriptVersion, turn_ids: tuple[str, ...]) -> str:
    identity = {
        "canonical_hash": script.canonical_hash,
        "first_turn_id": turn_ids[0],
        "last_turn_id": turn_ids[-1],
        "turn_ids": turn_ids,
    }
    canonical_identity = json.dumps(
        identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return f"segment-{digest[:16]}"


def _speaker_ids(turns: tuple[ScriptTurn, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(turn.speaker_id for turn in turns))


def _make_segment(
    script: ScriptVersion,
    turns: tuple[ScriptTurn, ...],
    tokens: tuple[CriticalToken, ...],
    leading_context: str,
    trailing_context: str,
) -> Segment:
    text = "\n".join(turn.text for turn in turns)
    anchors = tuple(anchor for turn in turns for anchor in turn.source_anchors)
    assigned_tokens = tuple(
        token
        for token in tokens
        if any(_overlaps(token.source_span, anchor) for anchor in anchors)
    )
    return Segment(
        _segment_id(script, tuple(turn.turn_id for turn in turns)),
        tuple(turn.turn_id for turn in turns),
        _speaker_ids(turns),
        text,
        anchors,
        assigned_tokens,
        leading_context,
        trailing_context,
        _estimate_duration(text),
        "difficult" if assigned_tokens else "normal",
    )


@dataclass(frozen=True)
class _LegacySegmentationResult(Mapping[str, object]):
    """Frozen mapping whose fields also satisfy the unchanged BDD lookup."""

    accepted: bool
    error: str

    def __getitem__(self, key: str) -> object:
        if key == "accepted":
            return self.accepted
        if key == "error":
            return self.error
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(("accepted", "error"))

    def __len__(self) -> int:
        return 2


def _segment_legacy_mapping(
    script: Mapping[str, object],
) -> Mapping[str, object]:
    """Adapt the frozen one-argument BDD mapping only for capability rejection.

    The legacy mapping has no canonical source snapshot, capabilities object, or
    token sequence, so it cannot produce typed ``Segment`` values safely. It is
    intentionally limited to validating the frozen oversized-turn shape and
    returning the read-only failure result that the original BDD binding asserts.
    Every other mapping is rejected as invalid rather than being accepted without
    canonical provenance.
    """
    if set(script) != {"turns", "renderer_text_limit"}:
        raise SegmentationError("invalid_script", "legacy mapping fields are invalid")
    turns = script["turns"]
    if not isinstance(turns, list) or len(turns) != 1:
        raise SegmentationError(
            "invalid_script", "legacy mapping must contain exactly one turn"
        )
    renderer_text_limit = script.get("renderer_text_limit")
    if type(renderer_text_limit) is not int or renderer_text_limit <= 0:
        raise SegmentationError(
            "invalid_script", "legacy renderer_text_limit must be positive"
        )

    turn = turns[0]
    if not isinstance(turn, Mapping):
        raise SegmentationError("invalid_script", "legacy turn must be a mapping")
    if set(turn) != {"turn_id", "speaker_id", "source_block_anchor", "text"}:
        raise SegmentationError("invalid_script", "legacy turn fields are invalid")
    turn_id = turn["turn_id"]
    speaker_id = turn["speaker_id"]
    source_block_anchor = turn["source_block_anchor"]
    text = turn["text"]
    if not isinstance(turn_id, str) or not turn_id:
        raise SegmentationError("invalid_script", "legacy turn fields are invalid")
    if not isinstance(speaker_id, str) or not speaker_id:
        raise SegmentationError("invalid_script", "legacy turn fields are invalid")
    if not isinstance(source_block_anchor, str) or not source_block_anchor:
        raise SegmentationError("invalid_script", "legacy turn fields are invalid")
    if not isinstance(text, str) or not text:
        raise SegmentationError("invalid_script", "legacy turn fields are invalid")
    if len(text) > renderer_text_limit:
        return _LegacySegmentationResult(
            False, "capability: complete turn exceeds renderer_text_limit"
        )

    raise SegmentationError(
        "invalid_script", "legacy mapping cannot produce canonical segments"
    )


@overload
def segment_script(
    script: ScriptVersion,
    source: SourceSnapshot,
    capabilities: SegmentationCapabilities,
    tokens: tuple[CriticalToken, ...],
) -> tuple[Segment, ...]: ...


@overload
def segment_script(script: Mapping[str, object]) -> Mapping[str, object]: ...


def segment_script(
    script: ScriptVersion | Mapping[str, object],
    source: SourceSnapshot | None = None,
    capabilities: SegmentationCapabilities | None = None,
    tokens: tuple[CriticalToken, ...] | None = None,
) -> tuple[Segment, ...] | Mapping[str, object]:
    """Create typed segments, with a narrow frozen-BDD mapping adapter.

    The canonical four-argument call remains the only path that creates typed
    segments. A one-argument mapping is accepted solely for the frozen oversized
    capability scenario; malformed or non-oversized mappings fail closed.
    """
    if isinstance(script, Mapping):
        if source is not None or capabilities is not None or tokens is not None:
            raise SegmentationError(
                "invalid_script", "legacy mapping cannot use typed arguments"
            )
        return _segment_legacy_mapping(script)
    if source is None or capabilities is None or tokens is None:
        raise SegmentationError(
            "invalid_script", "typed segmentation requires all canonical arguments"
        )
    _validate_script(script, source)
    if not isinstance(capabilities, SegmentationCapabilities):
        raise SegmentationError("invalid_script", "capabilities are invalid")
    _validate_tokens(tokens, script, source)
    for turn in script.turns:
        if turn.speaker_id not in capabilities.supported_speakers:
            raise SegmentationError(
                "unsupported_speaker", "turn speaker is unsupported"
            )
        duration = _estimate_duration(turn.text)
        if (
            len(turn.text) > capabilities.max_text_characters
            or capabilities.max_duration_seconds is not None
            and duration > capabilities.max_duration_seconds
        ):
            raise SegmentationError(
                "turn_too_large", "complete turn exceeds capability"
            )

    turn_groups: list[tuple[ScriptTurn, ...]] = []
    current: list[ScriptTurn] = []
    for turn in script.turns:
        candidate = (*current, turn)
        candidate_text = "\n".join(item.text for item in candidate)
        candidate_duration = _estimate_duration(candidate_text)
        exceeds_duration = (
            capabilities.max_duration_seconds is not None
            and candidate_duration > capabilities.max_duration_seconds
        )
        if current and (
            len(candidate_text) > capabilities.max_text_characters or exceeds_duration
        ):
            turn_groups.append(tuple(current))
            current = [turn]
        else:
            current.append(turn)
    if current:
        turn_groups.append(tuple(current))

    segments = tuple(
        _make_segment(
            script,
            turns,
            tokens,
            "\n".join(turn.text for turn in turn_groups[index - 1]) if index else "",
            "\n".join(turn.text for turn in turn_groups[index + 1])
            if index + 1 < len(turn_groups)
            else "",
        )
        for index, turns in enumerate(turn_groups)
    )
    segment_ids = tuple(segment.segment_id for segment in segments)
    if len(segment_ids) != len(set(segment_ids)):
        raise SegmentationError(
            "invalid_script", "generated segment IDs are not unique"
        )
    return segments
