"""Immutable, layered pronunciation lexicons."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from unicodedata import normalize

LexiconScope = Literal["episode", "project", "domain", "global"]
_SCOPE_ORDER: tuple[LexiconScope, ...] = ("episode", "project", "domain", "global")
_SCOPES = frozenset(_SCOPE_ORDER)
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})


class LexiconConflictError(ValueError):
    """Raised when a layer contains competing entries for one normalized key."""


def _non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class PronunciationEntry:
    """One versioned pronunciation declaration."""

    entry_id: str
    key: str
    spoken_form: str
    version: str

    def __post_init__(self) -> None:
        _non_empty_string("entry_id", self.entry_id)
        _non_empty_string("key", self.key)
        _non_empty_string("spoken_form", self.spoken_form)
        _non_empty_string("version", self.version)


@dataclass(frozen=True)
class PronunciationLexicon:
    """An immutable pronunciation layer."""

    scope: LexiconScope
    version: str
    entries: tuple[PronunciationEntry, ...]

    def __post_init__(self) -> None:
        if self.scope not in _SCOPES:
            raise ValueError(f"Unsupported lexicon scope: {self.scope}")
        _non_empty_string("lexicon version", self.version)
        entries = tuple(self.entries)
        if any(not isinstance(entry, PronunciationEntry) for entry in entries):
            raise ValueError("entries must contain PronunciationEntry values")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True)
class PronunciationResolution:
    """The selected pronunciation and immutable provenance."""

    normalized_key: str
    spoken_form: str
    scope: LexiconScope
    lexicon_version: str
    entry_id: str


def normalize_lexicon_key(value: str) -> str:
    """Normalize spelling variants to one deterministic lexicon key."""
    if not isinstance(value, str):
        raise ValueError("lexicon key must be a string")
    return " ".join(normalize("NFC", value).translate(_APOSTROPHES).casefold().split())


def resolve_pronunciation(
    key: str, layers: Mapping[LexiconScope, PronunciationLexicon]
) -> PronunciationResolution | None:
    """Resolve a key with fail-closed conflicts and fixed scope precedence."""
    normalized_key = normalize_lexicon_key(key)
    for scope in _SCOPE_ORDER:
        lexicon = layers.get(scope)
        if lexicon is None:
            continue
        matches = tuple(
            entry
            for entry in lexicon.entries
            if normalize_lexicon_key(entry.key) == normalized_key
        )
        if len(matches) > 1:
            raise LexiconConflictError(
                f"multiple {scope} pronunciations for normalized key {normalized_key!r}"
            )
        if matches:
            entry = matches[0]
            return PronunciationResolution(
                normalized_key=normalized_key,
                spoken_form=entry.spoken_form,
                scope=scope,
                lexicon_version=lexicon.version,
                entry_id=entry.entry_id,
            )
    return None
