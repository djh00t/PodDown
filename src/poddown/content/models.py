"""Immutable values for source-bound content preparation."""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from types import MappingProxyType
from typing import Literal

SourceBlockKind = Literal["heading", "paragraph", "list", "blockquote", "table", "code"]
_SOURCE_BLOCK_KINDS = frozenset(
    {"heading", "paragraph", "list", "blockquote", "table", "code"}
)
_PROFILE_FORMATS = frozenset({"narration", "dialogue"})
_SCRIPT_KINDS = frozenset({"factual", "editorial"})
_HASH = re.compile(r"^[0-9a-f]{64}$")
DOCUMENT_OVERRIDABLE_FIELDS = frozenset(
    {"format", "target_minutes", "style", "audio", "quality"}
)
_IMMUTABLE_METADATA_TYPES = (type(None), bool, int, float, str, bytes, date, time)


def _non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(name: str, value: object) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _hash(name: str, value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def freeze_value(value: object) -> object:
    """Recursively freeze metadata values without rewriting supported keys."""
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata mapping keys must be strings")
            frozen_key = key if type(key) is str else str(key)
            if frozen_key in frozen:
                raise ValueError(f"duplicate metadata key after freezing: {frozen_key}")
            frozen[frozen_key] = freeze_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(freeze_value(item) for item in value)
    if type(value) in _IMMUTABLE_METADATA_TYPES:
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, bytes):
        return bytes(value)
    raise TypeError(f"unsupported mutable metadata value: {type(value).__name__}")


def freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    """Return an immutable copy of a string-keyed metadata mapping."""
    frozen = freeze_value(value)
    if not isinstance(frozen, Mapping):
        raise ValueError("metadata must be a mapping")
    return frozen


def _anchors(name: str, value: object) -> tuple["SourceAnchor", ...]:
    if isinstance(value, str) or not isinstance(value, tuple | list):
        raise ValueError(f"{name} must be a sequence of SourceAnchor values")
    result = tuple(value)
    if any(not isinstance(anchor, SourceAnchor) for anchor in result):
        raise ValueError(f"{name} must contain SourceAnchor values")
    return result


@dataclass(frozen=True)
class SourceBlock:
    """A source-preserving Markdown block identified by UTF-8 byte bounds."""

    block_id: str
    kind: SourceBlockKind
    text: str
    start: int
    end: int

    def __post_init__(self) -> None:
        _non_empty_string("block_id", self.block_id)
        if self.kind not in _SOURCE_BLOCK_KINDS:
            raise ValueError(f"Unsupported source block kind: {self.kind}")
        _non_empty_string("text", self.text)
        start = _integer("block start", self.start)
        end = _integer("block end", self.end)
        if start < 0 or end < start:
            raise ValueError("block must be a non-negative half-open range")


@dataclass(frozen=True)
class SourceSnapshot:
    """The authoritative Markdown source and its parsed frontmatter."""

    source: str
    source_sha256: str
    frontmatter: Mapping[str, object]
    blocks: tuple[SourceBlock, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, str):
            raise ValueError("source must be a string")
        source_sha256 = _hash("source_sha256", self.source_sha256)
        expected = hashlib.sha256(self.source.encode("utf-8")).hexdigest()
        if source_sha256 != expected:
            raise ValueError("source_sha256 does not match source")
        frontmatter = freeze_mapping(self.frontmatter)
        try:
            blocks = tuple(self.blocks)
        except TypeError as error:
            raise ValueError("blocks must be a sequence") from error
        if any(not isinstance(block, SourceBlock) for block in blocks):
            raise ValueError("blocks must contain SourceBlock values")
        block_ids = [block.block_id for block in blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("source block IDs must be unique")
        source_bytes = self.source.encode("utf-8")
        for block in blocks:
            if block.end > len(source_bytes):
                raise ValueError("source block exceeds source byte length")
            try:
                block_text = source_bytes[block.start : block.end].decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("source block splits a UTF-8 character") from error
            if block_text != block.text:
                raise ValueError("source block text does not match its byte span")
        object.__setattr__(self, "source_sha256", source_sha256)
        object.__setattr__(self, "frontmatter", frontmatter)
        object.__setattr__(self, "blocks", blocks)


@dataclass(frozen=True)
class SourceAnchor:
    """A half-open UTF-8 byte range within one source block."""

    block_id: str
    start: int
    end: int

    def __post_init__(self) -> None:
        _non_empty_string("anchor block_id", self.block_id)
        start = _integer("anchor start", self.start)
        end = _integer("anchor end", self.end)
        if start < 0 or end < start:
            raise ValueError("anchor must be a non-negative half-open range")


@dataclass(frozen=True)
class VoiceAsset:
    """Locally verified voice-asset availability without provider details."""

    asset_id: str
    active: bool

    def __post_init__(self) -> None:
        _non_empty_string("voice asset ID", self.asset_id)
        if type(self.active) is not bool:
            raise ValueError("voice asset active must be a boolean")


@dataclass(frozen=True)
class VoiceConsent:
    """Current consent state for a locally identified voice asset."""

    asset_id: str
    valid: bool

    def __post_init__(self) -> None:
        _non_empty_string("voice consent asset ID", self.asset_id)
        if type(self.valid) is not bool:
            raise ValueError("voice consent valid must be a boolean")


@dataclass(frozen=True)
class SpeakerProfile:
    """A script speaker associated with an opaque approved voice asset."""

    speaker_id: str
    display_name: str
    voice_asset_id: str

    def __post_init__(self) -> None:
        _non_empty_string("speaker ID", self.speaker_id)
        _non_empty_string("speaker display name", self.display_name)
        _non_empty_string("speaker voice asset ID", self.voice_asset_id)


@dataclass(frozen=True)
class Profile:
    """Validated immutable content profile."""

    profile_id: str
    version: str
    format_type: Literal["narration", "dialogue"]
    target_minutes: int
    speakers: tuple[SpeakerProfile, ...]
    style: Mapping[str, object]
    audio: Mapping[str, object]
    quality: Mapping[str, object]
    document_overridable: frozenset[str]

    def __post_init__(self) -> None:
        _non_empty_string("profile ID", self.profile_id)
        _non_empty_string("profile version", self.version)
        if self.format_type not in _PROFILE_FORMATS:
            raise ValueError(f"Unsupported profile format: {self.format_type}")
        target_minutes = _integer("target_minutes", self.target_minutes)
        if not 1 <= target_minutes <= 180:
            raise ValueError("target_minutes must be between 1 and 180")
        try:
            speakers = tuple(self.speakers)
        except TypeError as error:
            raise ValueError("speakers must be a sequence") from error
        if not speakers or any(
            not isinstance(speaker, SpeakerProfile) for speaker in speakers
        ):
            raise ValueError("speakers must contain at least one SpeakerProfile")
        speaker_ids = [speaker.speaker_id for speaker in speakers]
        if len(speaker_ids) != len(set(speaker_ids)):
            raise ValueError("speaker IDs must be unique")
        if self.format_type == "dialogue" and len(speakers) < 2:
            raise ValueError("dialogue profiles require at least two speakers")
        try:
            overrides = tuple(self.document_overridable)
        except TypeError as error:
            raise ValueError("document_overridable must be a sequence") from error
        if any(not isinstance(field, str) for field in overrides):
            raise ValueError("document_overridable must contain strings")
        if len(overrides) != len(set(overrides)):
            raise ValueError("document_overridable contains duplicates")
        unknown = set(overrides) - DOCUMENT_OVERRIDABLE_FIELDS
        if unknown:
            raise ValueError(f"Unknown document-overridable fields: {sorted(unknown)}")
        object.__setattr__(self, "speakers", speakers)
        object.__setattr__(self, "style", freeze_mapping(self.style))
        object.__setattr__(self, "audio", freeze_mapping(self.audio))
        object.__setattr__(self, "quality", freeze_mapping(self.quality))
        object.__setattr__(self, "document_overridable", frozenset(overrides))


@dataclass(frozen=True)
class ScriptTurn:
    """One immutable source-bound script turn."""

    turn_id: str
    speaker_id: str
    text: str
    kind: Literal["factual", "editorial"]
    source_anchors: tuple[SourceAnchor, ...]
    claim_anchors: tuple[SourceAnchor, ...]

    def __post_init__(self) -> None:
        _non_empty_string("turn ID", self.turn_id)
        _non_empty_string("turn speaker ID", self.speaker_id)
        _non_empty_string("turn text", self.text)
        if self.kind not in _SCRIPT_KINDS:
            raise ValueError(f"Unsupported script turn kind: {self.kind}")
        object.__setattr__(
            self, "source_anchors", _anchors("source_anchors", self.source_anchors)
        )
        object.__setattr__(
            self, "claim_anchors", _anchors("claim_anchors", self.claim_anchors)
        )


@dataclass(frozen=True)
class ScriptVersion:
    """Canonical immutable script version."""

    script_id: str
    source_sha256: str
    profile_id: str
    turns: tuple[ScriptTurn, ...]
    canonical_hash: str

    def __post_init__(self) -> None:
        _non_empty_string("script ID", self.script_id)
        _hash("source_sha256", self.source_sha256)
        _non_empty_string("script profile ID", self.profile_id)
        try:
            turns = tuple(self.turns)
        except TypeError as error:
            raise ValueError("turns must be a sequence") from error
        if any(not isinstance(turn, ScriptTurn) for turn in turns):
            raise ValueError("turns must contain ScriptTurn values")
        turn_ids = [turn.turn_id for turn in turns]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("turn IDs must be unique")
        _hash("canonical_hash", self.canonical_hash)
        object.__setattr__(self, "turns", turns)
