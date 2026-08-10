"""Tests for platform-specific local voice resolution in the reference demo."""

from __future__ import annotations

import json
from pathlib import Path

from poddown.content.service import prepare_content
from poddown.demo import (
    _build_content_request,
    _load_reference_fixture,
    _local_voice_bindings,
    _reference_fixture_root,
    _ReferenceFixture,
    _spoken_text,
)
from poddown.qa.fidelity import evaluate_critical_tokens

REFERENCE_FIXTURE = Path(__file__).parents[2] / "integrations" / "reference-demo" / "v1"


def _fixture() -> _ReferenceFixture:
    return _load_reference_fixture(REFERENCE_FIXTURE)


def test_local_voice_bindings_use_macos_say_names() -> None:
    assert _local_voice_bindings(_fixture(), system_name="Darwin") == {
        "ref-host": "Samantha",
        "ref-analyst": "Daniel",
    }


def test_local_voice_bindings_use_espeak_names_on_non_macos_hosts() -> None:
    assert _local_voice_bindings(_fixture(), system_name="Linux") == {
        "ref-host": "en-us",
        "ref-analyst": "en-gb",
    }


def test_spoken_text_does_not_rewrite_generated_pronunciation_forms() -> None:
    fixture = _load_reference_fixture(_reference_fixture_root())
    prepared = prepare_content(_build_content_request(fixture))
    segment = next(
        segment
        for segment in prepared.segments
        if "LiDAR" in segment.text and "LIE-dar" in segment.text
    )
    transcript = _spoken_text(segment.text, prepared)

    assert "LIE-dar" in transcript
    assert "L I E-dar" in transcript
    assert evaluate_critical_tokens(
        tuple(token.expected_spoken_form for token in segment.critical_tokens),
        transcript,
    ).passed


def test_spoken_text_preserves_negation_fidelity_in_context() -> None:
    fixture = _load_reference_fixture(_reference_fixture_root())
    prepared = prepare_content(_build_content_request(fixture))
    segment = next(
        segment
        for segment in prepared.segments
        if "does not state that rain" in segment.text
    )
    expected = tuple(token.expected_spoken_form for token in segment.critical_tokens)
    transcript = _spoken_text(segment.text, prepared)

    assert evaluate_critical_tokens(expected, transcript).passed
    assert not evaluate_critical_tokens(
        expected, transcript.replace("does not state", "does state", 1)
    ).passed


def test_fixture_critical_token_evidence_matches_canonical_preparation() -> None:
    fixture = _load_reference_fixture(_reference_fixture_root())
    prepared = prepare_content(_build_content_request(fixture))
    declared = json.loads(
        (REFERENCE_FIXTURE / "adaptation.json").read_text(encoding="utf-8")
    )["expected_critical_tokens"]
    canonical = [
        {
            "surface": prepared.snapshot.source[
                token.source_span[0] : token.source_span[1]
            ],
            "source_form": prepared.snapshot.source[
                token.source_span[0] : token.source_span[1]
            ],
            "spoken_form": token.expected_spoken_form,
            "category": token.category,
            "occurrence_id": token.occurrence_id,
        }
        for token in prepared.tokens
    ]

    assert declared == canonical
