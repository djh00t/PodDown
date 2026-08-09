"""Immutable values for source-bound content preparation."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

SourceBlockKind = Literal["heading", "paragraph", "list", "blockquote", "table", "code"]


def freeze_value(value: object) -> object:
    """Recursively freeze metadata values held by public contracts."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(freeze_value(item) for item in value)
    return value


def freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    """Return an immutable copy of a metadata mapping."""
    return MappingProxyType({key: freeze_value(item) for key, item in value.items()})


@dataclass(frozen=True)
class SourceBlock:
    """A source-preserving Markdown block identified by UTF-8 byte bounds."""

    block_id: str
    kind: SourceBlockKind
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class SourceSnapshot:
    """The authoritative Markdown source and its parsed frontmatter."""

    source: str
    source_sha256: str
    frontmatter: Mapping[str, object]
    blocks: tuple[SourceBlock, ...]


@dataclass(frozen=True)
class SourceAnchor:
    """A half-open UTF-8 byte range within one source block."""

    block_id: str
    start: int
    end: int


@dataclass(frozen=True)
class VoiceAsset:
    """Locally verified voice-asset availability without provider details."""

    asset_id: str
    active: bool


@dataclass(frozen=True)
class VoiceConsent:
    """Current consent state for a locally identified voice asset."""

    asset_id: str
    valid: bool


@dataclass(frozen=True)
class SpeakerProfile:
    """A script speaker associated with an opaque approved voice asset."""

    speaker_id: str
    display_name: str
    voice_asset_id: str


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


@dataclass(frozen=True)
class ScriptTurn:
    """One immutable source-bound script turn."""

    turn_id: str
    speaker_id: str
    text: str
    kind: Literal["factual", "editorial"]
    source_anchors: tuple[SourceAnchor, ...]
    claim_anchors: tuple[SourceAnchor, ...]


@dataclass(frozen=True)
class ScriptVersion:
    """Canonical immutable script version."""

    script_id: str
    source_sha256: str
    profile_id: str
    turns: tuple[ScriptTurn, ...]
    canonical_hash: str
