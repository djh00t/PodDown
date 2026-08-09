"""Deterministic critical-token transcript verification."""

import unicodedata
from collections import Counter

from poddown.domain import FidelityResult

_NEGATIONS = frozenset({"neither", "never", "no", "nor", "not", "without"})
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


def _tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", text).casefold()
    normalized = normalized.replace("’", "'")
    for contraction, expanded in _NEGATION_CONTRACTIONS.items():
        normalized = normalized.replace(contraction, expanded)
    normalized = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return tuple(normalized.split())


def _occurrences(phrase: tuple[str, ...], transcript: tuple[str, ...]) -> int:
    if not phrase or len(phrase) > len(transcript):
        return 0
    phrase_has_negation = bool(_NEGATIONS.intersection(phrase))
    matches = 0
    for start in range(len(transcript) - len(phrase) + 1):
        if transcript[start : start + len(phrase)] != phrase:
            continue
        if (
            not phrase_has_negation
            and start > 0
            and transcript[start - 1] in _NEGATIONS
        ):
            continue
        matches += 1
    return matches


def evaluate_critical_tokens(
    expected: tuple[str, ...], transcript: str
) -> FidelityResult:
    """Require every expected spoken token occurrence in the transcript."""
    required = Counter(_tokens(value) for value in expected)
    if not required:
        return FidelityResult(True, 1.0, "none")

    actual_tokens = _tokens(transcript)
    expected_negations = sum(
        count * sum(token in _NEGATIONS for token in phrase)
        for phrase, count in required.items()
    )
    actual_negations = sum(token in _NEGATIONS for token in actual_tokens)
    matched = sum(
        min(count, _occurrences(phrase, actual_tokens))
        for phrase, count in required.items()
    )
    accuracy = matched / sum(required.values())
    passed = accuracy == 1.0 and actual_negations == expected_negations
    return FidelityResult(passed, accuracy, "none" if passed else "segment")
