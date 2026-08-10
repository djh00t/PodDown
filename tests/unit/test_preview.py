"""RED contracts for deterministic offline Markdown previews."""

from __future__ import annotations

import builtins
import hashlib
from dataclasses import FrozenInstanceError

import pytest

from poddown.preview import (
    PreviewResult,
    PreviewValidationError,
    preview_markdown,
    render_preview_json,
)

SOURCE = (
    b"---\n"
    b"poddown:\n"
    b"  profile: document-profile\n"
    b"---\n"
    b"# Caf\xc3\xa9\n\n"
    b"A deterministic preview.\n"
)


def test_preview_hashes_original_utf8_bytes_counts_blocks_and_never_calls_provider():
    """Catch preview normalizing source bytes or entering a provider boundary."""
    original = bytes(SOURCE)

    result = preview_markdown(
        SOURCE,
        source_name="episode.md",
        flag_profile=None,
        project_config={
            "profile": "project-profile",
            "profiles": ["document-profile"],
        },
        user_config={"profile": "user-profile"},
    )

    assert result == PreviewResult(
        source_sha256=hashlib.sha256(SOURCE).hexdigest(),
        profile_id="document-profile",
        source_bytes=len(SOURCE),
        block_count=2,
        provider_calls=0,
    )
    assert hashlib.sha256(SOURCE).hexdigest() == result.source_sha256
    assert original == SOURCE
    with pytest.raises(FrozenInstanceError):
        result.profile_id = "mutated-profile"  # type: ignore[misc]


def test_preview_profile_resolution_precedence():
    """Catch any lower-precedence profile silently winning resolution."""
    document_source = SOURCE
    body_only_source = b"# Body only\n"

    flag = preview_markdown(
        document_source,
        source_name="episode.md",
        flag_profile="flag-profile",
        project_config={
            "profile": "project-profile",
            "profiles": ["document-profile"],
        },
        user_config={"profile": "user-profile"},
    )
    document = preview_markdown(
        document_source,
        source_name="episode.md",
        flag_profile=None,
        project_config={
            "profile": "project-profile",
            "profiles": ["document-profile"],
        },
        user_config={"profile": "user-profile"},
    )
    project = preview_markdown(
        body_only_source,
        source_name="episode.md",
        flag_profile=None,
        project_config={"profile": "project-profile"},
        user_config={"profile": "user-profile"},
    )
    user = preview_markdown(
        body_only_source,
        source_name="episode.md",
        flag_profile=None,
        project_config=None,
        user_config={"profile": "user-profile"},
    )
    default = preview_markdown(
        body_only_source,
        source_name="episode.md",
        flag_profile=None,
        project_config=None,
        user_config=None,
    )

    assert [
        flag.profile_id,
        document.profile_id,
        project.profile_id,
        user.profile_id,
        default.profile_id,
    ] == [
        "flag-profile",
        "document-profile",
        "project-profile",
        "user-profile",
        "default",
    ]


def test_preview_rejects_unregistered_document_profile_with_other_config():
    """Catch unrelated project configuration implicitly authorizing a profile."""
    source = b"---\npoddown:\n  profile: missing-profile\n---\n# Unknown\n"

    with pytest.raises(
        PreviewValidationError, match="Unknown profile: missing-profile"
    ):
        preview_markdown(
            source,
            source_name="episode.md",
            flag_profile=None,
            project_config={"profile": "known-profile"},
            user_config={"setting": "unrelated"},
        )


def test_preview_does_not_enter_provider_network_or_storage_boundaries(monkeypatch):
    """Catch future preview changes importing paid or stateful execution paths."""
    original_import = builtins.__import__
    forbidden_prefixes = (
        "poddown.providers",
        "poddown.audio",
        "poddown.storage",
        "socket",
        "urllib",
    )

    def guarded_import(name, *args, **kwargs):
        if name.startswith(forbidden_prefixes):
            raise AssertionError(f"preview entered forbidden boundary: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = preview_markdown(
        b"# Local preview\n",
        source_name="episode.md",
        flag_profile=None,
        project_config=None,
        user_config=None,
    )

    assert result.provider_calls == 0


def test_preview_json_is_compact_sorted_utf8_and_contains_no_transient_data():
    """Catch JSON output that changes byte-for-byte across equivalent previews."""
    result = PreviewResult(
        source_sha256="abc123",
        profile_id="caf\u00e9-profile",
        source_bytes=17,
        block_count=2,
        provider_calls=0,
    )

    assert render_preview_json(result) == (
        b'{"block_count":2,"profile_id":"caf\xc3\xa9-profile","provider_calls":0,'
        b'"source_bytes":17,"source_sha256":"abc123"}'
    )


@pytest.mark.parametrize(
    ("source", "expected_error"),
    [
        (b"# Invalid \xff\n", "Source is not valid UTF-8"),
        (b"---\npoddown: [\n---\n# Broken\n", "Invalid YAML frontmatter"),
        (
            b"---\npoddown:\n  profile: unknown-profile\n---\n# Unknown\n",
            "Unknown profile: unknown-profile",
        ),
    ],
)
def test_preview_maps_invalid_source_to_stable_validation_error(source, expected_error):
    """Catch malformed input escaping as parser-specific exceptions."""
    with pytest.raises(PreviewValidationError) as raised:
        preview_markdown(
            source,
            source_name="episode.md",
            flag_profile=None,
            project_config=None,
            user_config=None,
        )

    assert raised.value.errors == (expected_error,)
    assert str(raised.value) == expected_error
