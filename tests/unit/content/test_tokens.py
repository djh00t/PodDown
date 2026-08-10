"""Tests for deterministic critical-token extraction."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.content.lexicon import (
    PronunciationEntry,
    PronunciationLexicon,
)
from poddown.content.tokens import (
    CriticalToken,
    TokenExtractionConflictError,
    extract_critical_tokens,
)
from poddown.qa.fidelity import evaluate_critical_tokens

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
                PronunciationEntry(
                    "opaque-name",
                    "Ada Lovelace",
                    "AY-da LUV-liss",
                    "1",
                    category="name",
                ),
                PronunciationEntry(
                    "opaque-organization",
                    "Atlas Robotics",
                    "AT-las robotics",
                    "1",
                    category="organization",
                ),
                PronunciationEntry(
                    "opaque-product",
                    "PodDown Studio",
                    "pod down studio",
                    "1",
                    category="product",
                ),
                PronunciationEntry(
                    "opaque-technical",
                    "LiDAR",
                    "LIE-dar",
                    "1",
                    category="technical_term",
                ),
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
        "one point six terabits per second",
        "twenty-one point five kilograms",
        "twelve point five percent",
        "August ninth, two thousand twenty-six",
        "four point two million dollars",
        "A A P L",
    ]
    assert [token.pronunciation_source for token in tokens[:6]] == [
        "project:project-7:opaque-name",
        "project:project-7:opaque-organization",
        "project:project-7:opaque-product",
        None,
        "project:project-7:opaque-technical",
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


def test_extract_critical_tokens_uses_utf8_byte_spans_and_unicode_normalization():
    """Character offsets after a multibyte prefix would point at the wrong bytes."""
    text = "é isn’t Cafe\u0301   O’Connor"
    lexicons = {
        "project": PronunciationLexicon(
            "project",
            "v1",
            (
                PronunciationEntry(
                    "opaque", "café o'connor", "cafe", "e1", category="name"
                ),
            ),
        )
    }

    tokens = extract_critical_tokens(text, lexicons)

    assert [(token.category, token.normalized) for token in tokens] == [
        ("negation", "isn't"),
        ("name", "café o'connor"),
    ]
    assert tokens[0].source_span == (3, 10)
    assert tokens[0].script_span == (3, 10)
    assert tokens[0].source_form == "isn’t"
    assert tokens[1].source_span == (11, 30)
    assert tokens[1].script_span == (11, 30)
    assert tokens[1].source_form == "Cafe\u0301   O’Connor"
    assert tokens[1].pronunciation_source == "project:v1:opaque"


def test_lexicon_matching_normalizes_once_per_source_character(monkeypatch):
    """Substring enumeration would normalize quadratically for a long script."""
    import poddown.content.tokens as tokens_module

    original = tokens_module.normalize_lexicon_key
    calls = 0

    def counted(value: str) -> str:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(tokens_module, "normalize_lexicon_key", counted)
    text = "ordinary " * 400
    lexicons = {
        "project": PronunciationLexicon(
            "project",
            "v1",
            (
                PronunciationEntry("one", "missing one", "one", "v1"),
                PronunciationEntry("two", "missing two", "two", "v1"),
            ),
        )
    }

    assert extract_critical_tokens(text, lexicons) == ()
    assert calls < len(text) * 4


def test_structural_categories_win_but_keep_lexicon_pronunciation_provenance():
    """A lexicon hint must not relabel date, currency, ticker, acronym, or number."""
    text = "2026-08-09 $AAPL SLAM 42"
    lexicons = {
        "episode": PronunciationLexicon(
            "episode",
            "episode-v1",
            (
                PronunciationEntry(
                    "date-id", "2026-08-09", "launch date", "e1", category="product"
                ),
                PronunciationEntry(
                    "ticker-id", "$AAPL", "apple shares", "e1", category="name"
                ),
                PronunciationEntry(
                    "acronym-id", "SLAM", "slam method", "e1", category="product"
                ),
                PronunciationEntry(
                    "number-id", "42", "the answer", "e1", category="name"
                ),
            ),
        )
    }

    tokens = extract_critical_tokens(text, lexicons)

    assert [token.category for token in tokens] == [
        "date",
        "ticker",
        "acronym",
        "number",
    ]
    assert [token.expected_spoken_form for token in tokens] == [
        "launch date",
        "apple shares",
        "slam method",
        "the answer",
    ]
    assert [token.pronunciation_source for token in tokens] == [
        "episode:episode-v1:date-id",
        "episode:episode-v1:ticker-id",
        "episode:episode-v1:acronym-id",
        "episode:episode-v1:number-id",
    ]


def test_structural_numeric_tokens_use_m0_compatible_verbalization():
    """Digit-preserving speech would fail the existing deterministic fidelity gate."""
    tokens = extract_critical_tokens(
        "1.6 Tbit/s, 21.5 kg, 99.7%, 2026-08-09, $4.2M, and 12 minutes."
    )

    assert [(token.category, token.normalized) for token in tokens] == [
        ("unit", "1.6 tbit/s"),
        ("unit", "21.5 kg"),
        ("percentage", "99.7%"),
        ("date", "2026-08-09"),
        ("currency", "$4.2m"),
        ("unit", "12 minutes"),
    ]
    assert [token.expected_spoken_form for token in tokens] == [
        "one point six terabits per second",
        "twenty-one point five kilograms",
        "ninety-nine point seven percent",
        "August ninth, two thousand twenty-six",
        "four point two million dollars",
        "twelve minutes",
    ]


def test_negations_cover_m0_words_contractions_and_boundaries():
    """Every M0 negation occurrence must survive, while notable is ordinary text."""
    text = (
        "not no never none without neither nor cannot can not isn't isn’t "
        "can't can’t notable"
    )

    tokens = extract_critical_tokens(text)

    assert [token.normalized for token in tokens] == [
        "not",
        "no",
        "never",
        "none",
        "without",
        "neither",
        "nor",
        "cannot",
        "can not",
        "isn't",
        "isn't",
        "can't",
        "can't",
    ]
    assert [token.occurrence_id for token in tokens] == [
        f"neg-{index:02d}" for index in range(1, 14)
    ]
    assert [token.expected_spoken_form for token in tokens] == [
        "not",
        "no",
        "never",
        "none",
        "without",
        "neither",
        "nor",
        "cannot",
        "can not",
        "is not",
        "is not",
        "can not",
        "can not",
    ]


def test_token_sequence_exposes_read_only_legacy_aliases():
    """Frozen Task 1 bindings need tuple semantics plus deterministic aliases."""
    result = extract_critical_tokens("not and not")

    assert isinstance(result, tuple)
    assert result.tokens is result
    assert result.manifest.deterministic is True
    assert result.manifest.occurrence_count == 2
    assert result[0].spoken_form == "not"
    assert result[0].source_form == "not"
    assert result[0].source_span_start == 0
    assert result[0].source_span_end == 3
    assert result[0].script_span_start == 0
    assert result[0].script_span_end == 3


def test_critical_token_rejects_invalid_values():
    """Malformed token evidence must fail closed rather than reach a renderer."""
    with pytest.raises(ValueError):
        CriticalToken("", "number", "1", (0, 1), (0, 1), "one", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CriticalToken("tok-1", "not-a-category", "1", (0, 1), (0, 1), "one", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CriticalToken("tok-1", "number", "1", (1, 1), (1, 1), "one", None)
    with pytest.raises(ValueError):
        CriticalToken("tok-1", "number", "1", (0, 1), (0, 1), "", None)


class _StringLike:
    def __str__(self) -> str:
        return "lexicon:v1:opaque"


class _StringSubclass(str):
    pass


def test_critical_token_accepts_none_or_nonblank_builtin_string_provenance():
    """Optional provenance remains valid while a real string is preserved exactly."""
    without_source = CriticalToken("tok-1", "number", "1", (0, 1), (0, 1), "one", None)
    with_source = CriticalToken(
        "tok-2", "number", "1", (0, 1), (0, 1), "one", " lexicon:v1:id "
    )

    assert without_source.pronunciation_source is None
    assert with_source.pronunciation_source == " lexicon:v1:id "


@pytest.mark.parametrize(
    "provenance",
    [1, b"lexicon:v1:id", _StringLike(), _StringSubclass("lexicon:v1:id")],
)
def test_critical_token_rejects_non_string_provenance(provenance):
    """Provenance must not admit values that only look or behave like strings."""
    with pytest.raises(ValueError, match="pronunciation_source"):
        CriticalToken("tok-1", "number", "1", (0, 1), (0, 1), "one", provenance)


def test_nested_lexicon_matches_fail_closed_instead_of_dropping_an_occurrence():
    """Keeping only the longer or shorter configured name loses declared evidence."""
    lexicons = {
        "project": PronunciationLexicon(
            "project",
            "v1",
            (
                PronunciationEntry("short", "Atlas", "atlas", "e1", category="name"),
                PronunciationEntry(
                    "long",
                    "Atlas Robotics",
                    "atlas robotics",
                    "e1",
                    category="organization",
                ),
            ),
        )
    }

    with pytest.raises(TokenExtractionConflictError, match="overlap"):
        extract_critical_tokens("Atlas Robotics", lexicons)


def test_partial_lexicon_overlap_with_structural_span_fails_closed():
    """A partial date match must not inherit pronunciation from an incomplete span."""
    lexicons = {
        "project": PronunciationLexicon(
            "project",
            "v1",
            (PronunciationEntry("partial", "2026-08", "partial date", "e1"),),
        )
    }

    with pytest.raises(TokenExtractionConflictError, match="overlap"):
        extract_critical_tokens("2026-08-09", lexicons)


@pytest.mark.parametrize("malformed", [[], ""])
def test_falsy_supplied_lexicons_are_not_treated_as_no_layers(malformed):
    """Only None means no layers; falsy malformed values must be rejected."""
    with pytest.raises(TypeError, match="mapping"):
        extract_critical_tokens("SLAM", malformed)  # type: ignore[arg-type]


def test_cannot_expected_speech_passes_m0_fidelity_as_one_token():
    """The required negation occurrence must retain M0's one-token cannot form."""
    token = extract_critical_tokens("cannot")[0]

    assert token.category == "negation"
    assert token.normalized == "cannot"
    assert token.expected_spoken_form == "cannot"
    assert (
        evaluate_critical_tokens((token.expected_spoken_form,), "cannot").passed is True
    )


def test_date_speech_is_fully_verbal_and_passes_m0_fidelity():
    """Digits in an expected date would fail the deterministic spoken contract."""
    token = extract_critical_tokens("2026-08-09")[0]

    assert token.category == "date"
    assert token.expected_spoken_form == "August ninth, two thousand twenty-six"
    assert not any(character.isdigit() for character in token.expected_spoken_form)
    assert (
        evaluate_critical_tokens(
            (token.expected_spoken_form,),
            "The launch date is August ninth, two thousand twenty-six.",
        ).passed
        is True
    )
