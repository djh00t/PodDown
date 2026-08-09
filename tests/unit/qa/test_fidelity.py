"""Tests for exact critical-token fidelity."""

import pytest

from poddown.qa.fidelity import evaluate_critical_tokens


@pytest.mark.parametrize(
    ("expected", "transcript"),
    [
        (("café",), "The CAFE\N{COMBINING ACUTE ACCENT} opened."),
        (("twenty-one kilograms",), "Twenty one kilograms."),
        (("see one",), "We selected SEE ONE, today."),
    ],
)
def test_equivalent_spoken_phrases_pass_normalization(expected, transcript):
    """Presentation-only Unicode, case, or punctuation changes are harmless."""
    result = evaluate_critical_tokens(expected, transcript)

    assert result.passed is True
    assert result.accuracy == 1.0
    assert result.rerender_scope == "none"


def test_repeated_expected_tokens_require_repeated_transcript_occurrences():
    """One spoken occurrence cannot satisfy two canonical occurrences."""
    result = evaluate_critical_tokens(("TSMC", "TSMC"), "TSMC announced")

    assert result.passed is False
    assert result.accuracy == 0.5
    assert result.rerender_scope == "segment"


def test_repeated_expected_negations_are_counted_per_occurrence():
    result = evaluate_critical_tokens(
        ("not supported", "not supported"),
        "not supported and not supported",
    )

    assert result.passed is True
    assert result.accuracy == 1.0


@pytest.mark.parametrize(
    ("expected", "transcript"),
    [
        (("is not supported",), "is supported"),
        (("is supported",), "is not supported"),
        (("one point six terabit",), "one point six terabits"),
    ],
)
def test_changed_negation_or_partial_word_match_fails(expected, transcript):
    """Negation changes and substring-only matches cannot pass a factual gate."""
    result = evaluate_critical_tokens(expected, transcript)

    assert result.passed is False
    assert result.rerender_scope == "segment"


def test_empty_critical_token_set_is_complete():
    """A segment with no critical tokens has perfect critical-token fidelity."""
    result = evaluate_critical_tokens((), "ordinary narration")

    assert result.passed is True
    assert result.accuracy == 1.0
    assert result.rerender_scope == "none"


@pytest.mark.parametrize(
    "transcript",
    [
        "The system is supported, but it is not certified.",
        "The system is supported without certification.",
        "The system is supported, though it isn't certified.",
    ],
)
def test_inserted_negation_anywhere_in_segment_fails(transcript):
    result = evaluate_critical_tokens(("system is supported",), transcript)

    assert result.passed is False
    assert result.rerender_scope == "segment"
