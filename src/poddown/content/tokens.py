"""Deterministic critical-token extraction for canonical scripts."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from poddown.content.lexicon import (
    LexiconScope,
    PronunciationLexicon,
    normalize_lexicon_key,
    resolve_pronunciation,
)

TokenCategory = Literal[
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
]
_DIGITS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}
_NEGATION = re.compile(r"\b(?:not|no|never|none|without)\b", re.IGNORECASE)
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_PERCENTAGE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)%")
_CURRENCY = re.compile(r"\$(\d+(?:\.\d+)?)([KMB])?\b")
_UNIT = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s+(Tbit/s|kg)\b", re.IGNORECASE)
_TICKER = re.compile(r"\$([A-Z]{1,5})\b")
_ACRONYM = re.compile(r"\b(?:[A-Z]{2,}|[A-Z]\d+)\b")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")


@dataclass(frozen=True)
class CriticalToken:
    """One source-bound occurrence requiring exact spoken treatment."""

    occurrence_id: str
    category: TokenCategory
    normalized: str
    source_span: tuple[int, int]
    script_span: tuple[int, int] | None
    expected_spoken_form: str
    pronunciation_source: str | None


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    category: TokenCategory
    spoken_form: str
    pronunciation_source: str | None = None


def _category_for_entry(entry_id: str) -> TokenCategory:
    category = entry_id.replace("_", "-").split("-", 1)[0]
    if category == "name":
        return "name"
    if category == "organization":
        return "organization"
    if category == "product":
        return "product"
    return "technical_term"


def _add_matches(
    matches: list[_Match], occupied: list[tuple[int, int]], candidates: list[_Match]
) -> None:
    for candidate in candidates:
        if any(
            candidate.start < end and start < candidate.end for start, end in occupied
        ):
            continue
        matches.append(candidate)
        occupied.append((candidate.start, candidate.end))


def _lexicon_matches(
    text: str, lexicons: Mapping[LexiconScope, PronunciationLexicon]
) -> list[_Match]:
    candidates: list[_Match] = []
    for lexicon in lexicons.values():
        for entry in lexicon.entries:
            pattern = re.compile(
                r"(?<!\w)" + re.escape(entry.key) + r"(?!\w)", re.IGNORECASE
            )
            for match in pattern.finditer(text):
                resolution = resolve_pronunciation(match.group(), lexicons)
                if resolution is None:
                    continue
                candidates.append(
                    _Match(
                        match.start(),
                        match.end(),
                        _category_for_entry(resolution.entry_id),
                        resolution.spoken_form,
                        f"{resolution.scope}:{resolution.lexicon_version}:{resolution.entry_id}",
                    )
                )
    return sorted(candidates, key=lambda item: (item.start, -(item.end - item.start)))


def _spoken_acronym(value: str) -> str:
    return " ".join(_DIGITS.get(character, character) for character in value)


def _spoken_unit(value: str, unit: str) -> str:
    words = {"tbit/s": "terabits per second", "kg": "kilograms"}
    return f"{value} {words[unit.casefold()]}"


def _spoken_currency(value: str, suffix: str) -> str:
    words = {
        "": "dollars",
        "K": "thousand dollars",
        "M": "million dollars",
        "B": "billion dollars",
    }
    return f"{value} {words[suffix]}"


def extract_critical_tokens(
    text: str, lexicons: Mapping[LexiconScope, PronunciationLexicon] | None = None
) -> tuple[CriticalToken, ...]:
    """Extract all critical occurrences with deterministic precedence and IDs."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    layers: Mapping[LexiconScope, PronunciationLexicon] = lexicons or {}
    matches: list[_Match] = []
    occupied: list[tuple[int, int]] = []
    _add_matches(matches, occupied, _lexicon_matches(text, layers))
    _add_matches(
        matches,
        occupied,
        [
            _Match(
                match.start(),
                match.end(),
                "date",
                date(int(match[1]), int(match[2]), int(match[3])).strftime(
                    "%B %-d, %Y"
                ),
            )
            for match in _DATE.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(match.start(), match.end(), "percentage", f"{match[1]} percent")
            for match in _PERCENTAGE.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(
                match.start(),
                match.end(),
                "currency",
                _spoken_currency(match[1], match[2] or ""),
            )
            for match in _CURRENCY.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(match.start(), match.end(), "unit", _spoken_unit(match[1], match[2]))
            for match in _UNIT.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(match.start(), match.end(), "ticker", _spoken_acronym(match[1]))
            for match in _TICKER.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(
                match.start(), match.end(), "acronym", _spoken_acronym(match.group())
            )
            for match in _ACRONYM.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(match.start(), match.end(), "negation", match.group().casefold())
            for match in _NEGATION.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(match.start(), match.end(), "number", match.group())
            for match in _NUMBER.finditer(text)
        ],
    )

    tokens: list[CriticalToken] = []
    token_count = 0
    negation_count = 0
    for match in sorted(matches, key=lambda item: item.start):
        if match.category == "negation":
            negation_count += 1
            occurrence_id = f"neg-{negation_count:02d}"
        else:
            token_count += 1
            occurrence_id = f"tok-{token_count:02d}"
        normalized = normalize_lexicon_key(text[match.start : match.end])
        tokens.append(
            CriticalToken(
                occurrence_id=occurrence_id,
                category=match.category,
                normalized=normalized,
                source_span=(match.start, match.end),
                script_span=(match.start, match.end),
                expected_spoken_form=match.spoken_form,
                pronunciation_source=match.pronunciation_source,
            )
        )
    return tuple(tokens)
