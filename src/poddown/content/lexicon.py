"""Immutable, layered pronunciation lexicons."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NamedTuple
from unicodedata import normalize

LexiconScope = Literal["episode", "project", "domain", "global"]
LexiconTokenCategory = Literal["name", "organization", "product", "technical_term"]

_SCOPE_ORDER: tuple[LexiconScope, ...] = ("episode", "project", "domain", "global")
_SCOPES = frozenset(_SCOPE_ORDER)
_CATEGORIES = frozenset({"name", "organization", "product", "technical_term"})
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "＇": "'"})


class LexiconConflictError(ValueError):
    """Raised when a layer contains competing entries for one normalized key."""


def _non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _scope(value: object) -> LexiconScope:
    if value not in _SCOPES:
        raise ValueError(f"Unsupported lexicon scope: {value}")
    return value


class LegacyPronunciationResult(NamedTuple):
    """Tuple-compatible result retained for the frozen Task 1 bindings."""

    selected_layer: str | None
    selected_scope: str | None
    selected_spoken_form: str | None
    version: str | None
    entry_id: str | None
    accepted: bool
    error: str | None
    normalized_key: str

    @property
    def scope(self) -> str | None:
        return self.selected_scope

    @property
    def spoken(self) -> str | None:
        return self.selected_spoken_form

    @property
    def spoken_form(self) -> str | None:
        return self.selected_spoken_form

    @property
    def lexicon_version(self) -> str | None:
        return self.version

    @property
    def selected_version(self) -> str | None:
        return self.version

    @property
    def selected_entry_id(self) -> str | None:
        return self.entry_id


def _legacy_failure(normalized_key: str, error: str) -> LegacyPronunciationResult:
    return LegacyPronunciationResult(
        selected_layer=None,
        selected_scope=None,
        selected_spoken_form=None,
        version=None,
        entry_id=None,
        accepted=False,
        error=error,
        normalized_key=normalized_key,
    )


def _legacy_layers(
    key: str, rows: list[Mapping[str, object]]
) -> LegacyPronunciationResult:
    normalized_key = normalize_lexicon_key(key)
    if not normalized_key:
        return _legacy_failure(normalized_key, "key must be a non-empty string")

    matching: dict[LexiconScope, list[dict[str, str]]] = {
        scope: [] for scope in _SCOPE_ORDER
    }
    try:
        for row in rows:
            if not isinstance(row, Mapping):
                return _legacy_failure(normalized_key, "legacy entry must be a mapping")
            row_scope = row.get("scope", row.get("layer"))
            row_layer = row.get("layer", row_scope)
            scope = _scope(row_scope)
            if row_layer != scope:
                return _legacy_failure(
                    normalized_key, "legacy layer and scope must match"
                )
            entry_id = _non_empty_string("entry_id", row.get("entry_id"))
            spoken_form = _non_empty_string("spoken_form", row.get("spoken_form"))
            version = _non_empty_string("version", row.get("version"))
            row_key = row.get("normalized", row.get("key", key))
            normalized_row_key = normalize_lexicon_key(
                _non_empty_string("key", row_key)
            )
            if normalized_row_key == normalized_key:
                matching[scope].append(
                    {
                        "layer": str(row_layer),
                        "scope": str(scope),
                        "spoken_form": spoken_form,
                        "version": version,
                        "entry_id": entry_id,
                    }
                )
    except (TypeError, ValueError) as error:
        return _legacy_failure(normalized_key, str(error))

    for scope in _SCOPE_ORDER:
        entries = matching[scope]
        if len(entries) > 1:
            return _legacy_failure(
                normalized_key,
                f"conflict: multiple {scope} pronunciations for {normalized_key!r}",
            )
        if entries:
            entry = entries[0]
            return LegacyPronunciationResult(
                selected_layer=entry["layer"],
                selected_scope=entry["scope"],
                selected_spoken_form=entry["spoken_form"],
                version=entry["version"],
                entry_id=entry["entry_id"],
                accepted=True,
                error=None,
                normalized_key=normalized_key,
            )
    return LegacyPronunciationResult(
        selected_layer=None,
        selected_scope=None,
        selected_spoken_form=None,
        version=None,
        entry_id=None,
        accepted=True,
        error=None,
        normalized_key=normalized_key,
    )


def _validate_mapping_layers(
    layers: Mapping[LexiconScope, "PronunciationLexicon"],
) -> None:
    for scope, lexicon in layers.items():
        _scope(scope)
        if not isinstance(lexicon, PronunciationLexicon):
            raise ValueError("lexicon layers must contain PronunciationLexicon values")
        if lexicon.scope != scope:
            raise ValueError("lexicon mapping key must match lexicon scope")


@dataclass(frozen=True)
class PronunciationEntry:
    """One versioned pronunciation declaration."""

    entry_id: str
    key: str
    spoken_form: str
    version: str
    category: LexiconTokenCategory = "technical_term"

    def __post_init__(self) -> None:
        _non_empty_string("entry_id", self.entry_id)
        key = _non_empty_string("key", self.key)
        _non_empty_string("spoken_form", self.spoken_form)
        _non_empty_string("version", self.version)
        if not normalize_lexicon_key(key):
            raise ValueError("key must contain non-whitespace content")
        if not isinstance(self.category, str) or self.category not in _CATEGORIES:
            raise ValueError(f"Unsupported lexicon token category: {self.category}")


@dataclass(frozen=True)
class PronunciationLexicon:
    """An immutable pronunciation layer."""

    scope: LexiconScope
    version: str
    entries: tuple[PronunciationEntry, ...]

    def __post_init__(self) -> None:
        _scope(self.scope)
        _non_empty_string("lexicon version", self.version)
        try:
            entries = tuple(self.entries)
        except TypeError as error:
            raise ValueError("entries must be a sequence") from error
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

    def __post_init__(self) -> None:
        normalized_key = _non_empty_string("normalized_key", self.normalized_key)
        if normalize_lexicon_key(normalized_key) != normalized_key:
            raise ValueError("normalized_key must already be normalized")
        _non_empty_string("spoken_form", self.spoken_form)
        _scope(self.scope)
        _non_empty_string("lexicon_version", self.lexicon_version)
        _non_empty_string("entry_id", self.entry_id)


def normalize_lexicon_key(value: str) -> str:
    """Normalize spelling variants to one deterministic lexicon key."""
    if not isinstance(value, str):
        raise ValueError("lexicon key must be a string")
    return " ".join(normalize("NFC", value).translate(_APOSTROPHES).casefold().split())


def _resolve_typed_pronunciation(
    key: str, layers: Mapping[LexiconScope, PronunciationLexicon]
) -> PronunciationResolution | None:
    _validate_mapping_layers(layers)
    normalized_key = normalize_lexicon_key(key)
    if not normalized_key:
        return None
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


def resolve_pronunciation(
    key: str,
    layers: Mapping[LexiconScope, PronunciationLexicon] | list[Mapping[str, object]],
) -> PronunciationResolution | LegacyPronunciationResult | None:
    """Resolve typed layers strictly or adapt frozen legacy layer rows."""
    if isinstance(layers, Mapping):
        return _resolve_typed_pronunciation(key, layers)
    if isinstance(layers, list):
        return _legacy_layers(key, layers)
    raise TypeError("layers must be a typed mapping or legacy list")
