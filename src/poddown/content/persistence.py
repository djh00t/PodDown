"""Durable, secret-free persistence for canonical prepared content."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from datetime import date, time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, cast

from poddown.content.models import (
    Profile,
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    SourceBlock,
    SourceSnapshot,
    SpeakerProfile,
)
from poddown.content.segmentation import Segment
from poddown.content.service import ContentPreparationResult
from poddown.content.tokens import CriticalToken

_KEY = frozenset("0123456789abcdef")
_SCHEMA_VERSION = 1


class PreparedContentPersistenceError(RuntimeError):
    """Raised when canonical prepared content cannot be safely persisted."""


def _json_value(value: object) -> object:
    """Convert immutable model values into tagged JSON-native values."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((_json_value(item) for item in value), key=repr)
    if isinstance(value, bytes):
        return {
            "__poddown_type__": "bytes",
            "value": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, date) and not isinstance(value, time):
        return {"__poddown_type__": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"__poddown_type__": "time", "value": value.isoformat()}
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise PreparedContentPersistenceError(
        f"unsupported prepared-content metadata type: {type(value).__name__}"
    )


def _from_json(value: object) -> object:
    if isinstance(value, list):
        return [_from_json(item) for item in value]
    if isinstance(value, dict):
        marker = value.get("__poddown_type__")
        if marker == "bytes":
            encoded = value.get("value")
            if not isinstance(encoded, str):
                raise PreparedContentPersistenceError("encoded bytes are malformed")
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise PreparedContentPersistenceError(
                    "encoded bytes are malformed"
                ) from error
        if marker == "date":
            raw = value.get("value")
            if not isinstance(raw, str):
                raise PreparedContentPersistenceError("encoded date is malformed")
            try:
                return date.fromisoformat(raw)
            except ValueError as error:
                raise PreparedContentPersistenceError(
                    "encoded date is malformed"
                ) from error
        if marker == "time":
            raw = value.get("value")
            if not isinstance(raw, str):
                raise PreparedContentPersistenceError("encoded time is malformed")
            try:
                return time.fromisoformat(raw)
            except ValueError as error:
                raise PreparedContentPersistenceError(
                    "encoded time is malformed"
                ) from error
        return {key: _from_json(item) for key, item in value.items()}
    return value


def _anchor(anchor: SourceAnchor) -> dict[str, object]:
    return {
        "block_id": anchor.block_id,
        "start": anchor.start,
        "end": anchor.end,
    }


def _anchor_from(value: object) -> SourceAnchor:
    if not isinstance(value, Mapping):
        raise PreparedContentPersistenceError("prepared anchor is malformed")
    try:
        return SourceAnchor(
            cast(str, value["block_id"]),
            cast(int, value["start"]),
            cast(int, value["end"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError("prepared anchor is malformed") from error


def _snapshot(snapshot: SourceSnapshot) -> dict[str, object]:
    return {
        "source": snapshot.source,
        "source_sha256": snapshot.source_sha256,
        "frontmatter": _json_value(snapshot.frontmatter),
        "blocks": [
            {
                "block_id": block.block_id,
                "kind": block.kind,
                "text": block.text,
                "start": block.start,
                "end": block.end,
            }
            for block in snapshot.blocks
        ],
    }


def _snapshot_from(value: object) -> SourceSnapshot:
    if not isinstance(value, Mapping):
        raise PreparedContentPersistenceError("prepared source snapshot is malformed")
    raw_blocks = value.get("blocks")
    frontmatter = _from_json(value.get("frontmatter"))
    if not isinstance(raw_blocks, list) or not isinstance(frontmatter, Mapping):
        raise PreparedContentPersistenceError("prepared source snapshot is malformed")
    try:
        blocks = tuple(
            SourceBlock(
                cast(str, item["block_id"]),
                cast(
                    Literal[
                        "heading", "paragraph", "list", "blockquote", "table", "code"
                    ],
                    item["kind"],
                ),
                cast(str, item["text"]),
                cast(int, item["start"]),
                cast(int, item["end"]),
            )
            for item in raw_blocks
            if isinstance(item, Mapping)
        )
        if len(blocks) != len(raw_blocks):
            raise ValueError("prepared source block is malformed")
        return SourceSnapshot(
            cast(str, value["source"]),
            cast(str, value["source_sha256"]),
            cast(Mapping[str, object], frontmatter),
            blocks,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError(
            "prepared source snapshot is malformed"
        ) from error


def _profile(profile: Profile) -> dict[str, object]:
    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "format_type": profile.format_type,
        "target_minutes": profile.target_minutes,
        "speakers": [
            {
                "speaker_id": speaker.speaker_id,
                "display_name": speaker.display_name,
                "voice_asset_id": speaker.voice_asset_id,
            }
            for speaker in profile.speakers
        ],
        "style": _json_value(profile.style),
        "audio": _json_value(profile.audio),
        "quality": _json_value(profile.quality),
        "document_overridable": sorted(profile.document_overridable),
    }


def _profile_from(value: object) -> Profile:
    if not isinstance(value, Mapping):
        raise PreparedContentPersistenceError("prepared profile is malformed")
    raw_speakers = value.get("speakers")
    style = _from_json(value.get("style"))
    audio = _from_json(value.get("audio"))
    quality = _from_json(value.get("quality"))
    if (
        not isinstance(raw_speakers, list)
        or not isinstance(style, Mapping)
        or not isinstance(audio, Mapping)
        or not isinstance(quality, Mapping)
        or not isinstance(value.get("document_overridable"), list)
    ):
        raise PreparedContentPersistenceError("prepared profile is malformed")
    try:
        speakers = tuple(
            SpeakerProfile(
                cast(str, item["speaker_id"]),
                cast(str, item["display_name"]),
                cast(str, item["voice_asset_id"]),
            )
            for item in raw_speakers
            if isinstance(item, Mapping)
        )
        if len(speakers) != len(raw_speakers):
            raise ValueError("prepared speaker is malformed")
        return Profile(
            cast(str, value["profile_id"]),
            cast(str, value["version"]),
            cast(Literal["narration", "dialogue"], value["format_type"]),
            cast(int, value["target_minutes"]),
            speakers,
            cast(Mapping[str, object], style),
            cast(Mapping[str, object], audio),
            cast(Mapping[str, object], quality),
            frozenset(cast(str, item) for item in value["document_overridable"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError(
            "prepared profile is malformed"
        ) from error


def _turn(turn: ScriptTurn) -> dict[str, object]:
    return {
        "turn_id": turn.turn_id,
        "speaker_id": turn.speaker_id,
        "text": turn.text,
        "kind": turn.kind,
        "source_anchors": [_anchor(item) for item in turn.source_anchors],
        "claim_anchors": [_anchor(item) for item in turn.claim_anchors],
    }


def _turn_from(value: object) -> ScriptTurn:
    if not isinstance(value, Mapping):
        raise PreparedContentPersistenceError("prepared script turn is malformed")
    source_anchors = value.get("source_anchors")
    claim_anchors = value.get("claim_anchors")
    if not isinstance(source_anchors, list) or not isinstance(claim_anchors, list):
        raise PreparedContentPersistenceError("prepared script turn is malformed")
    try:
        return ScriptTurn(
            cast(str, value["turn_id"]),
            cast(str, value["speaker_id"]),
            cast(str, value["text"]),
            cast(Literal["factual", "editorial"], value["kind"]),
            tuple(_anchor_from(item) for item in source_anchors),
            tuple(_anchor_from(item) for item in claim_anchors),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError(
            "prepared script turn is malformed"
        ) from error


def _script(script: ScriptVersion) -> dict[str, object]:
    return {
        "script_id": script.script_id,
        "source_sha256": script.source_sha256,
        "profile_id": script.profile_id,
        "turns": [_turn(item) for item in script.turns],
        "canonical_hash": script.canonical_hash,
    }


def _script_from(value: object) -> ScriptVersion:
    if not isinstance(value, Mapping) or not isinstance(value.get("turns"), list):
        raise PreparedContentPersistenceError("prepared script is malformed")
    try:
        return ScriptVersion(
            cast(str, value["script_id"]),
            cast(str, value["source_sha256"]),
            cast(str, value["profile_id"]),
            tuple(_turn_from(item) for item in value["turns"]),
            cast(str, value["canonical_hash"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError("prepared script is malformed") from error


def _token(token: CriticalToken) -> dict[str, object]:
    return {
        "occurrence_id": token.occurrence_id,
        "category": token.category,
        "normalized": token.normalized,
        "source_span": list(token.source_span),
        "script_span": list(token.script_span)
        if token.script_span is not None
        else None,
        "expected_spoken_form": token.expected_spoken_form,
        "pronunciation_source": token.pronunciation_source,
        "source_form": token.source_form,
    }


def _token_from(value: object) -> CriticalToken:
    if not isinstance(value, Mapping):
        raise PreparedContentPersistenceError("prepared token is malformed")
    source_span = value.get("source_span")
    script_span = value.get("script_span")
    if not isinstance(source_span, list) or (
        script_span is not None and not isinstance(script_span, list)
    ):
        raise PreparedContentPersistenceError("prepared token is malformed")
    try:
        return CriticalToken(
            cast(str, value["occurrence_id"]),
            cast(
                Literal[
                    "name",
                    "organization",
                    "product",
                    "acronym",
                    "technical_term",
                    "number",
                    "currency",
                    "percentage",
                    "date",
                    "unit",
                    "ticker",
                    "negation",
                ],
                value["category"],
            ),
            cast(str, value["normalized"]),
            (cast(int, source_span[0]), cast(int, source_span[1])),
            (
                (cast(int, script_span[0]), cast(int, script_span[1]))
                if script_span is not None
                else None
            ),
            cast(str, value["expected_spoken_form"]),
            cast(str | None, value["pronunciation_source"]),
            cast(str, value.get("source_form", "")),
        )
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError("prepared token is malformed") from error


def _segment(segment: Segment) -> dict[str, object]:
    return {
        "segment_id": segment.segment_id,
        "turn_ids": list(segment.turn_ids),
        "speaker_ids": list(segment.speaker_ids),
        "text": segment.text,
        "source_anchors": [_anchor(item) for item in segment.source_anchors],
        "critical_token_ids": [item.occurrence_id for item in segment.critical_tokens],
        "leading_context": segment.leading_context,
        "trailing_context": segment.trailing_context,
        "estimated_duration_seconds": segment.estimated_duration_seconds,
        "difficulty": segment.difficulty,
    }


def _segment_from(value: object, tokens: Mapping[str, CriticalToken]) -> Segment:
    if not isinstance(value, Mapping):
        raise PreparedContentPersistenceError("prepared segment is malformed")
    turn_ids = value.get("turn_ids")
    speaker_ids = value.get("speaker_ids")
    raw_anchors = value.get("source_anchors")
    raw_token_ids = value.get("critical_token_ids")
    if not all(
        isinstance(item, list)
        for item in (turn_ids, speaker_ids, raw_anchors, raw_token_ids)
    ):
        raise PreparedContentPersistenceError("prepared segment is malformed")
    turn_values = cast(list[object], turn_ids)
    speaker_values = cast(list[object], speaker_ids)
    anchor_values = cast(list[object], raw_anchors)
    token_id_values = cast(list[object], raw_token_ids)
    try:
        critical_tokens = tuple(tokens[cast(str, item)] for item in token_id_values)
        return Segment(
            cast(str, value["segment_id"]),
            tuple(cast(str, item) for item in turn_values),
            tuple(cast(str, item) for item in speaker_values),
            cast(str, value["text"]),
            tuple(_anchor_from(item) for item in anchor_values),
            critical_tokens,
            cast(str, value["leading_context"]),
            cast(str, value["trailing_context"]),
            cast(float, value["estimated_duration_seconds"]),
            cast(Literal["normal", "difficult"], value["difficulty"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError(
            "prepared segment is malformed"
        ) from error


def _record(result: ContentPreparationResult) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "snapshot": _snapshot(result.snapshot),
        "profile": _profile(result.profile),
        "script": _script(result.script),
        "tokens": [_token(item) for item in result.tokens],
        "segments": [_segment(item) for item in result.segments],
        "manifest": _json_value(result.manifest),
        "manifest_sha256": result.manifest_sha256,
    }


def _result(value: object) -> ContentPreparationResult:
    if not isinstance(value, Mapping) or value.get("schema_version") != _SCHEMA_VERSION:
        raise PreparedContentPersistenceError("prepared content record is malformed")
    raw_tokens = value.get("tokens")
    raw_segments = value.get("segments")
    manifest = _from_json(value.get("manifest"))
    if not isinstance(raw_tokens, list) or not isinstance(raw_segments, list):
        raise PreparedContentPersistenceError("prepared content record is malformed")
    if not isinstance(manifest, Mapping):
        raise PreparedContentPersistenceError("prepared content manifest is malformed")
    tokens = tuple(_token_from(item) for item in raw_tokens)
    token_map = {item.occurrence_id: item for item in tokens}
    if len(token_map) != len(tokens):
        raise PreparedContentPersistenceError("prepared token IDs are not unique")
    try:
        result = ContentPreparationResult(
            _snapshot_from(value.get("snapshot")),
            _profile_from(value.get("profile")),
            _script_from(value.get("script")),
            tokens,
            tuple(_segment_from(item, token_map) for item in raw_segments),
            cast(Mapping[str, object], manifest),
            cast(str, value["manifest_sha256"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PreparedContentPersistenceError(
            "prepared content record is malformed"
        ) from error
    serialized = result.manifest.get("serialized")
    checksum = result.manifest.get("checksum")
    if (
        not isinstance(serialized, str)
        or hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        != result.manifest_sha256
        or checksum != result.manifest_sha256
    ):
        raise PreparedContentPersistenceError(
            "prepared content manifest checksum failed"
        )
    return result


class PreparedContentStore:
    """Atomically persist and reload one canonical preparation by identity key."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, result: ContentPreparationResult) -> None:
        path = self._path(key)
        if not isinstance(result, ContentPreparationResult):
            raise PreparedContentPersistenceError("prepared content is not canonical")
        record = _record(result)
        payload = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise PreparedContentPersistenceError(
                    "prepared content record is unavailable"
                ) from error
            if existing != payload:
                raise PreparedContentPersistenceError(
                    "prepared content record conflicts"
                )
            return
        with NamedTemporaryFile(
            dir=self._root, mode="w", encoding="utf-8", delete=False
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            temporary_path = Path(temporary.name)
        try:
            try:
                temporary_path.replace(path)
            except FileExistsError as error:
                if path.read_text(encoding="utf-8") != payload:
                    raise PreparedContentPersistenceError(
                        "prepared content record conflicts"
                    ) from error
        finally:
            temporary_path.unlink(missing_ok=True)

    def load(self, key: str) -> ContentPreparationResult:
        path = self._path(key)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PreparedContentPersistenceError(
                "prepared content record is unavailable"
            ) from error
        return _result(value)

    def _path(self, key: str) -> Path:
        if (
            not isinstance(key, str)
            or len(key) != 64
            or any(character not in _KEY for character in key)
        ):
            raise PreparedContentPersistenceError("prepared content key is invalid")
        return self._root / f"{key}.json"


__all__ = ["PreparedContentPersistenceError", "PreparedContentStore"]
