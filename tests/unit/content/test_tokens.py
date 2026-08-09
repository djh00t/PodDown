"""Tests for deterministic critical-token extraction."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.content.lexicon import (
    PronunciationEntry,
    PronunciationLexicon,
)
from poddown.content.tokens import CriticalToken, extract_critical_tokens

TEXT = (
    "Ada Lovelace from Atlas Robotics shipped PodDown Studio with SLAM, LiDAR, "
    "and C1. It is not slow, not cheap: 1.6 Tbit/s, 21.5 kg, 12.5%, "
    "2026-08-09, $4.2M, and $AAPL."
)


def _lexicons() -> dict[str, PronunciationLexicon]:
    return {
        "project": PronunciationLexicon(
            scope="project",
            version="project-7",
            entries=(
                PronunciationEntry("name-ada", "Ada Lovelace", "AY-da LUV-liss", "1"),
                PronunciationEntry(
                    "organization-atlas", "Atlas Robotics", "AT-las robotics", "1"
                ),
                PronunciationEntry(
                    "product-poddown", "PodDown Studio", "pod down studio", "1"
                ),
                PronunciationEntry("technical-term-lidar", "LiDAR", "LIE-dar", "1"),
            ),
        )
    }


def test_extract_critical_tokens_preserves_all_categories_spans_and_speech():
    """A specific match regressing to generic text loses critical speech context."""
    tokens = extract_critical_tokens(TEXT, _lexicons())

    assert [(token.category, token.normalized) for token in tokens] == [
        ("name", "ada lovelace"),
        ("organization", "atlas robotics"),
        ("product", "poddown studio"),
        ("acronym", "slam"),
        ("technical_term", "lidar"),
        ("acronym", "c1"),
        ("negation", "not"),
        ("negation", "not"),
        ("unit", "1.6 tbit/s"),
        ("unit", "21.5 kg"),
        ("percentage", "12.5%"),
        ("date", "2026-08-09"),
        ("currency", "$4.2m"),
        ("ticker", "$aapl"),
    ]
    assert [token.occurrence_id for token in tokens] == [
        "tok-01",
        "tok-02",
        "tok-03",
        "tok-04",
        "tok-05",
        "tok-06",
        "neg-01",
        "neg-02",
        "tok-07",
        "tok-08",
        "tok-09",
        "tok-10",
        "tok-11",
        "tok-12",
    ]
    assert [(token.source_span, token.script_span) for token in tokens] == [
        ((0, 12), (0, 12)),
        ((18, 32), (18, 32)),
        ((41, 55), (41, 55)),
        ((61, 65), (61, 65)),
        ((67, 72), (67, 72)),
        ((78, 80), (78, 80)),
        ((88, 91), (88, 91)),
        ((98, 101), (98, 101)),
        ((109, 119), (109, 119)),
        ((121, 128), (121, 128)),
        ((130, 135), (130, 135)),
        ((137, 147), (137, 147)),
        ((149, 154), (149, 154)),
        ((160, 165), (160, 165)),
    ]
    assert [token.expected_spoken_form for token in tokens] == [
        "AY-da LUV-liss",
        "AT-las robotics",
        "pod down studio",
        "S L A M",
        "LIE-dar",
        "C one",
        "not",
        "not",
        "1.6 terabits per second",
        "21.5 kilograms",
        "12.5 percent",
        "August 9, 2026",
        "4.2 million dollars",
        "A A P L",
    ]
    assert [token.pronunciation_source for token in tokens[:6]] == [
        "project:project-7:name-ada",
        "project:project-7:organization-atlas",
        "project:project-7:product-poddown",
        None,
        "project:project-7:technical-term-lidar",
        None,
    ]


def test_extract_critical_tokens_preserves_repeated_generic_number_occurrences():
    """Deduplication would remove a source occurrence from repair checks."""
    tokens = extract_critical_tokens("C1 has 7 nodes, and 7 are not ready.")

    assert [
        (token.category, token.normalized, token.occurrence_id) for token in tokens
    ] == [
        ("acronym", "c1", "tok-01"),
        ("number", "7", "tok-02"),
        ("number", "7", "tok-03"),
        ("negation", "not", "neg-01"),
    ]


def test_extract_critical_tokens_is_deterministic_and_critical_values_are_frozen():
    """Replaying identical text must retain identities and immutable evidence."""
    first = extract_critical_tokens("SLAM is not silent.")
    second = extract_critical_tokens("SLAM is not silent.")

    assert first == second
    assert isinstance(first[0], CriticalToken)
    with pytest.raises(FrozenInstanceError):
        first[0].normalized = "changed"  # type: ignore[misc]
