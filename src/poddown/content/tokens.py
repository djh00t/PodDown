"""Deterministic critical-token extraction for canonical scripts."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from unicodedata import category as unicode_category

from poddown.content.lexicon import (
    LexiconScope,
    PronunciationEntry,
    PronunciationLexicon,
    PronunciationResolution,
    _validate_mapping_layers,
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
_STRUCTURAL_CATEGORIES = frozenset(
    {
        "date",
        "percentage",
        "currency",
        "unit",
        "ticker",
        "acronym",
        "negation",
        "number",
    }
)
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
_SMALL_NUMBERS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_ORDINALS = (
    "",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
    "twentieth",
    "twenty-first",
    "twenty-second",
    "twenty-third",
    "twenty-fourth",
    "twenty-fifth",
    "twenty-sixth",
    "twenty-seventh",
    "twenty-eighth",
    "twenty-ninth",
    "thirtieth",
    "thirty-first",
)
_UNIT_SPOKEN = {
    "tbit/s": "terabits per second",
    "tb/s": "terabytes per second",
    "kg": "kilograms",
    "kilogram": "kilogram",
    "kilograms": "kilograms",
    "hertz": "hertz",
    "hz": "hertz",
    "minute": "minute",
    "minutes": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "second": "second",
    "seconds": "seconds",
    "sec": "seconds",
    "secs": "seconds",
}
_NEGATION_CONTRACTIONS = {
    "aren't": "are not",
    "can't": "can not",
    "couldn't": "could not",
    "didn't": "did not",
    "doesn't": "does not",
    "don't": "do not",
    "hadn't": "had not",
    "hasn't": "has not",
    "haven't": "have not",
    "isn't": "is not",
    "mustn't": "must not",
    "shouldn't": "should not",
    "wasn't": "was not",
    "weren't": "were not",
    "won't": "will not",
    "wouldn't": "would not",
}
_TOKEN_CATEGORIES = frozenset(
    {
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
    }
)
_APOSTROPHE_PATTERN = r"['’‘ʼ＇]"
_NEGATION_ALTERNATIVES = [
    r"can\s+not",
    r"cannot",
    *[
        contraction.replace("'", _APOSTROPHE_PATTERN)
        for contraction in _NEGATION_CONTRACTIONS
    ],
    r"not",
    r"no",
    r"never",
    r"none",
    r"without",
    r"neither",
    r"nor",
]
_NEGATION = re.compile(
    r"(?<!\w)(?:" + "|".join(_NEGATION_ALTERNATIVES) + r")(?!\w)",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_PERCENTAGE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)%")
_CURRENCY = re.compile(r"(?<!\w)\$(\d+(?:\.\d+)?)([KMB])?(?!\w)")
_UNIT = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s+((?:Tbit/s|TB/s|kg|kilograms?|hertz|Hz|minutes?|mins?|seconds?|secs?))\b",
    re.IGNORECASE,
)
_TICKER = re.compile(r"\$([A-Z]{1,5})\b")
_ACRONYM = re.compile(r"\b(?:[A-Z]{2,}|[A-Z]\d+)\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


class TokenExtractionError(ValueError):
    """Raised when a critical-looking structural value is malformed."""


class TokenExtractionConflictError(TokenExtractionError):
    """Raised when declared token spans overlap ambiguously."""


def _validate_span(
    name: str, value: tuple[int, int] | None, *, allow_none: bool
) -> tuple[int, int] | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{name} must be a half-open span tuple")
    start, end = value
    if type(start) is not int or type(end) is not int or start < 0 or start >= end:
        raise ValueError(f"{name} must be a non-empty half-open span")
    return value


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
    _source_form: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.occurrence_id, str) or not self.occurrence_id.strip():
            raise ValueError("occurrence_id must be a non-empty string")
        if not isinstance(self.category, str) or self.category not in _TOKEN_CATEGORIES:
            raise ValueError(f"Unsupported token category: {self.category}")
        if not isinstance(self.normalized, str) or not self.normalized.strip():
            raise ValueError("normalized must be a non-empty string")
        _validate_span("source_span", self.source_span, allow_none=False)
        _validate_span("script_span", self.script_span, allow_none=True)
        if (
            not isinstance(self.expected_spoken_form, str)
            or not self.expected_spoken_form.strip()
        ):
            raise ValueError("expected_spoken_form must be a non-empty string")
        if (
            self.pronunciation_source is not None
            and not self.pronunciation_source.strip()
        ):
            raise ValueError("pronunciation_source must be non-empty when present")
        if not isinstance(self._source_form, str):
            raise ValueError("source form must be a string")

    @property
    def spoken_form(self) -> str:
        """Legacy Task 1 alias for the canonical expected spoken form."""
        return self.expected_spoken_form

    @property
    def source_form(self) -> str:
        """Legacy Task 1 alias retaining the original source spelling."""
        return self._source_form or self.normalized

    @property
    def source_span_start(self) -> int:
        return self.source_span[0]

    @property
    def source_span_end(self) -> int:
        return self.source_span[1]

    @property
    def script_span_start(self) -> int | None:
        return None if self.script_span is None else self.script_span[0]

    @property
    def script_span_end(self) -> int | None:
        return None if self.script_span is None else self.script_span[1]


@dataclass(frozen=True)
class TokenManifest:
    """Deterministic compatibility metadata for a token sequence."""

    deterministic: bool
    occurrence_count: int

    def __post_init__(self) -> None:
        if type(self.deterministic) is not bool:
            raise ValueError("deterministic must be a boolean")
        if type(self.occurrence_count) is not int or self.occurrence_count < 0:
            raise ValueError("occurrence_count must be a non-negative integer")


class CriticalTokenSequence(tuple[CriticalToken, ...]):
    """Immutable tuple result with the frozen Task 1 compatibility aliases."""

    __slots__ = ()

    @property
    def tokens(self) -> "CriticalTokenSequence":
        return self

    @property
    def manifest(self) -> TokenManifest:
        return TokenManifest(deterministic=True, occurrence_count=len(self))


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    category: TokenCategory
    spoken_form: str
    pronunciation_source: str | None = None


def _is_word_character(value: str) -> bool:
    return value == "_" or unicode_category(value).startswith(("L", "M", "N"))


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


def _spoken_integer(value: int) -> str:
    if value < 0:
        return f"minus {_spoken_integer(-value)}"
    if value < 20:
        return _SMALL_NUMBERS[value]
    if value < 100:
        return _TENS[value // 10] + (
            f"-{_SMALL_NUMBERS[value % 10]}" if value % 10 else ""
        )
    if value < 1000:
        remainder = value % 100
        suffix = f" {_spoken_integer(remainder)}" if remainder else ""
        return f"{_SMALL_NUMBERS[value // 100]} hundred{suffix}"
    for scale, scale_name in (
        (1_000_000_000, "billion"),
        (1_000_000, "million"),
        (1000, "thousand"),
    ):
        if value >= scale:
            remainder = value % scale
            suffix = f" {_spoken_integer(remainder)}" if remainder else ""
            return f"{_spoken_integer(value // scale)} {scale_name}{suffix}"
    raise AssertionError("unreachable number scale")


def _spoken_number(value: str) -> str:
    sign = ""
    unsigned = value
    if value.startswith(("-", "+")):
        sign = "minus " if value[0] == "-" else "plus "
        unsigned = value[1:]
    integer, separator, fraction = unsigned.partition(".")
    spoken = _spoken_integer(int(integer))
    if separator:
        spoken += " point " + " ".join(_DIGITS[digit] for digit in fraction)
    return sign + spoken


def _spoken_acronym(value: str) -> str:
    return " ".join(_DIGITS.get(character, character) for character in value)


def _spoken_date(year: str, month: str, day: str) -> str:
    try:
        month_number = int(month)
        day_number = int(day)
        year_number = int(year)
        if not 1 <= month_number <= 12:
            raise ValueError
        if day_number < 1:
            raise ValueError
        # Gregorian leap-year and month boundaries, without platform formatting.
        month_days = (
            31,
            29
            if year_number % 4 == 0
            and (year_number % 100 != 0 or year_number % 400 == 0)
            else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        )
        if day_number > month_days[month_number - 1]:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise TokenExtractionError("malformed ISO date token") from error
    return (
        f"{_MONTHS[month_number - 1]} {_ORDINALS[day_number]}, "
        f"{_spoken_integer(year_number)}"
    )


def _spoken_currency(value: str, suffix: str) -> str:
    units = {
        "": "dollars",
        "K": "thousand dollars",
        "M": "million dollars",
        "B": "billion dollars",
    }
    try:
        unit = units[suffix.upper()]
    except KeyError as error:
        raise TokenExtractionError("unsupported currency unit") from error
    return f"{_spoken_number(value)} {unit}"


def _spoken_unit(value: str, unit: str) -> str:
    normalized_unit = unit.casefold()
    try:
        spoken_unit = _UNIT_SPOKEN[normalized_unit]
    except KeyError as error:
        raise TokenExtractionError("unsupported measurement unit") from error
    return f"{_spoken_number(value)} {spoken_unit}"


def _byte_span(text: str, start: int, end: int) -> tuple[int, int]:
    return (
        len(text[:start].encode("utf-8")),
        len(text[:end].encode("utf-8")),
    )


def _entry_for_resolution(
    resolution: PronunciationResolution,
    layers: Mapping[LexiconScope, PronunciationLexicon],
) -> PronunciationEntry:
    scope = resolution.scope
    entry_id = resolution.entry_id
    for entry in layers[scope].entries:
        if entry.entry_id == entry_id:
            return entry
    raise ValueError("selected pronunciation entry is missing from its lexicon")


def _lexicon_matches(
    text: str, lexicons: Mapping[LexiconScope, PronunciationLexicon]
) -> list[_Match]:
    candidates: dict[tuple[int, int, str], _Match] = {}
    entries = sorted(
        (entry for lexicon in lexicons.values() for entry in lexicon.entries),
        key=lambda entry: (normalize_lexicon_key(entry.key), entry.entry_id),
    )
    for entry in entries:
        normalized_entry_key = normalize_lexicon_key(entry.key)
        for start in range(len(text)):
            if text[start].isspace() or (
                start > 0 and _is_word_character(text[start - 1])
            ):
                continue
            for end in range(start + 1, len(text) + 1):
                if text[end - 1].isspace() or (
                    end < len(text) and _is_word_character(text[end])
                ):
                    continue
                if normalize_lexicon_key(text[start:end]) != normalized_entry_key:
                    continue
                resolution = resolve_pronunciation(text[start:end], lexicons)
                if resolution is None:
                    continue
                if not isinstance(resolution, PronunciationResolution):
                    raise ValueError(
                        "typed lexicon resolution returned a legacy result"
                    )
                selected_entry = _entry_for_resolution(resolution, lexicons)
                candidates[(start, end, selected_entry.entry_id)] = _Match(
                    start=start,
                    end=end,
                    category=selected_entry.category,
                    spoken_form=resolution.spoken_form,
                    pronunciation_source=(
                        f"{resolution.scope}:{resolution.lexicon_version}:{resolution.entry_id}"
                    ),
                )
                break
    return sorted(
        candidates.values(),
        key=lambda match: (match.start, -(match.end - match.start), match.end),
    )


def _structural_matches(text: str) -> list[_Match]:
    matches: list[_Match] = []
    occupied: list[tuple[int, int]] = []
    _add_matches(
        matches,
        occupied,
        [
            _Match(
                match.start(),
                match.end(),
                "date",
                _spoken_date(match[1], match[2], match[3]),
            )
            for match in _DATE.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(
                match.start(),
                match.end(),
                "percentage",
                f"{_spoken_number(match[1])} percent",
            )
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
            _Match(
                match.start(),
                match.end(),
                "unit",
                _spoken_unit(match[1], match[2]),
            )
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
                match.start(),
                match.end(),
                "acronym",
                _spoken_acronym(match.group()),
            )
            for match in _ACRONYM.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(
                match.start(),
                match.end(),
                "negation",
                _negation_spoken(match.group()),
            )
            for match in _NEGATION.finditer(text)
        ],
    )
    _add_matches(
        matches,
        occupied,
        [
            _Match(match.start(), match.end(), "number", _spoken_number(match.group()))
            for match in _NUMBER.finditer(text)
        ],
    )
    return matches


def _negation_spoken(value: str) -> str:
    normalized = normalize_lexicon_key(value)
    if normalized in _NEGATION_CONTRACTIONS:
        return _NEGATION_CONTRACTIONS[normalized]
    if normalized == "cannot":
        return "cannot"
    if normalized == "can not":
        return "can not"
    return normalized


def _merge_lexicon_matches(
    matches: list[_Match], lexicon_matches: list[_Match]
) -> None:
    for candidate in lexicon_matches:
        overlaps = [
            index
            for index, existing in enumerate(matches)
            if candidate.start < existing.end and existing.start < candidate.end
        ]
        if not overlaps:
            matches.append(candidate)
            continue
        structural_indices = [
            index
            for index in overlaps
            if matches[index].category in _STRUCTURAL_CATEGORIES
        ]
        if structural_indices:
            if len(overlaps) != 1:
                raise TokenExtractionConflictError(
                    "lexicon span overlaps multiple token spans"
                )
            structural_index = structural_indices[0]
            structural = matches[structural_index]
            if (candidate.start, candidate.end) != (
                structural.start,
                structural.end,
            ):
                raise TokenExtractionConflictError(
                    "lexicon span partially overlaps a structural token span"
                )
            matches[structural_index] = _Match(
                start=structural.start,
                end=structural.end,
                category=structural.category,
                spoken_form=candidate.spoken_form,
                pronunciation_source=candidate.pronunciation_source,
            )
            continue
        raise TokenExtractionConflictError("overlapping lexicon token spans")


def extract_critical_tokens(
    text: str, lexicons: Mapping[LexiconScope, PronunciationLexicon] | None = None
) -> CriticalTokenSequence:
    """Extract all critical occurrences with deterministic precedence and IDs."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if lexicons is None:
        layers: Mapping[LexiconScope, PronunciationLexicon] = {}
    elif not isinstance(lexicons, Mapping):
        raise TypeError("lexicons must be a mapping or None")
    else:
        layers = lexicons
    _validate_mapping_layers(layers)

    matches = _structural_matches(text)
    _merge_lexicon_matches(matches, _lexicon_matches(text, layers))
    matches.sort(key=lambda match: match.start)

    tokens: list[CriticalToken] = []
    token_count = 0
    negation_count = 0
    for match in matches:
        if match.category == "negation":
            negation_count += 1
            occurrence_id = f"neg-{negation_count:02d}"
        else:
            token_count += 1
            occurrence_id = f"tok-{token_count:02d}"
        source_span = _byte_span(text, match.start, match.end)
        tokens.append(
            CriticalToken(
                occurrence_id=occurrence_id,
                category=match.category,
                normalized=normalize_lexicon_key(text[match.start : match.end]),
                source_span=source_span,
                script_span=source_span,
                expected_spoken_form=match.spoken_form,
                pronunciation_source=match.pronunciation_source,
                _source_form=text[match.start : match.end],
            )
        )
    return CriticalTokenSequence(tokens)
