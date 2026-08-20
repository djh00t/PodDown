"""Tests for canonical Markdown intake."""

import hashlib

import pytest

from poddown.intake import validate_markdown


@pytest.mark.parametrize(
    ("source", "error_fragment"),
    [
        ("---\npoddown: [\n---\n# Broken\n", "YAML"),
        ("---\npoddown: dialogue\n---\n# Wrong type\n", "object"),
        ("---\npoddown: {}\n---\n# Missing profile\n", "profile"),
        (
            "---\npoddown:\n  profile: missing\n---\n# Unknown profile\n",
            "Unknown profile",
        ),
        (
            "---\npoddown:\n  profile: technical-dialogue\n  surprise: true\n"
            "---\n# Unknown key\n",
            "Unknown PodDown key",
        ),
    ],
)
def test_invalid_frontmatter_is_rejected_before_provider_use(source, error_fragment):
    """Malformed or unsupported metadata cannot reach paid providers."""
    result = validate_markdown(source, {"technical-dialogue"})

    assert result.accepted is False
    assert result.source_sha256 is None
    assert result.provider_calls == 0
    assert any(error_fragment in error for error in result.errors)


def test_source_hash_uses_exact_utf8_document_bytes():
    """Changing even trailing source whitespace changes provenance identity."""
    source = "---\npoddown:\n  profile: technical-dialogue\n---\n# Mapping robots\n"

    first = validate_markdown(source, {"technical-dialogue"})
    second = validate_markdown(source + "\n", {"technical-dialogue"})

    assert first.accepted is True
    assert first.source_sha256 == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert second.source_sha256 != first.source_sha256


def test_body_only_markdown_uses_resolved_default_profile():
    source = "# Mapping robots\n\nBody-only CommonMark remains canonical.\n"

    result = validate_markdown(
        source, {"technical-dialogue"}, default_profile="technical-dialogue"
    )

    assert result.accepted is True
    assert result.source_sha256 == hashlib.sha256(source.encode()).hexdigest()


def test_reference_fixture_metadata_is_accepted():
    """Established reference-fixture metadata remains valid PodDown input."""
    source = """---
poddown:
  profile: reference-demo-dialogue-v1
  episode_id: reference-demo-episode-v1
  duration_minutes: 12
  source_blocks: 125
  source_turns: 125
  source_locale: en-AU
---
# Reference demo
"""

    result = validate_markdown(source, {"reference-demo-dialogue-v1"})

    assert result.accepted is True
    assert result.source_sha256 == hashlib.sha256(source.encode()).hexdigest()


@pytest.mark.parametrize(
    "override",
    ["'': spoken", "term: ''"],
)
def test_pronunciation_overrides_require_nonempty_keys_and_values(override):
    source = (
        "---\npoddown:\n  profile: technical-dialogue\n"
        f"  pronunciation_overrides:\n    {override}\n---\n# Document\n"
    )

    result = validate_markdown(source, {"technical-dialogue"})

    assert result.accepted is False
    assert "pronunciation" in result.errors[0].casefold()
