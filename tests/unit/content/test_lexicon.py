"""Tests for deterministic layered pronunciation lexicons."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.content.lexicon import (
    LexiconConflictError,
    PronunciationEntry,
    PronunciationLexicon,
    PronunciationResolution,
    normalize_lexicon_key,
    resolve_pronunciation,
)


def test_normalize_lexicon_key_canonicalizes_unicode_case_apostrophes_and_space():
    """Equivalent written names must resolve to the same immutable key."""
    assert normalize_lexicon_key("  Cafe\u0301  O\u2019Connor ") == "café o'connor"


def test_resolve_pronunciation_prefers_episode_and_records_provenance():
    """Dropping the highest-priority override would speak the wrong form."""
    layers = {
        "global": PronunciationLexicon(
            scope="global",
            version="global-1",
            entries=(PronunciationEntry("global-c1", "C1", "cee one", "entry-1"),),
        ),
        "domain": PronunciationLexicon(
            scope="domain",
            version="domain-2",
            entries=(PronunciationEntry("domain-c1", "c1", "control one", "entry-2"),),
        ),
        "project": PronunciationLexicon(
            scope="project",
            version="project-3",
            entries=(PronunciationEntry("project-c1", "C1", "see one", "entry-3"),),
        ),
        "episode": PronunciationLexicon(
            scope="episode",
            version="episode-4",
            entries=(
                PronunciationEntry("episode-c1", " C1 ", "chapter one", "entry-4"),
            ),
        ),
    }

    assert resolve_pronunciation("c1", layers) == PronunciationResolution(
        normalized_key="c1",
        spoken_form="chapter one",
        scope="episode",
        lexicon_version="episode-4",
        entry_id="episode-c1",
    )


def test_resolve_pronunciation_rejects_same_layer_normalized_conflict():
    """Choosing one duplicate LiDAR entry would make pronunciation non-deterministic."""
    lexicon = PronunciationLexicon(
        scope="project",
        version="project-1",
        entries=(
            PronunciationEntry("lidar-1", "LiDAR", "LIE-dar", "entry-1"),
            PronunciationEntry("lidar-2", "lidar", "lee-DAR", "entry-2"),
        ),
    )

    with pytest.raises(LexiconConflictError, match="lidar"):
        resolve_pronunciation("LiDAR", {"project": lexicon})


def test_resolve_pronunciation_returns_none_for_unconfigured_key():
    """An unrelated key must not acquire a fabricated pronunciation."""
    assert resolve_pronunciation("SLAM", {}) is None


def test_lexicon_values_are_frozen():
    """Changing published layer data after construction would violate replay safety."""
    entry = PronunciationEntry("lidar", "LiDAR", "LIE-dar", "entry-1")
    lexicon = PronunciationLexicon("domain", "domain-1", (entry,))

    with pytest.raises(FrozenInstanceError):
        entry.spoken_form = "lee-DAR"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        lexicon.version = "domain-2"  # type: ignore[misc]


def test_category_hint_is_optional_and_entry_id_remains_opaque():
    """Category must come from typed metadata rather than an ID naming convention."""
    default_entry = PronunciationEntry("opaque-id", "LiDAR", "LIE-dar", "v1")
    named_entry = PronunciationEntry(
        "also-opaque", "Ada Lovelace", "AY-da", "v1", category="name"
    )

    assert default_entry.category == "technical_term"
    assert named_entry.category == "name"
    assert named_entry.entry_id == "also-opaque"


def test_mapping_scope_must_match_each_lexicon_scope():
    """A mislabeled layer must not silently alter precedence or provenance."""
    project_lexicon = PronunciationLexicon(
        scope="project",
        version="project-1",
        entries=(PronunciationEntry("opaque", "C1", "see one", "v1"),),
    )

    with pytest.raises(ValueError, match="scope"):
        resolve_pronunciation("C1", {"domain": project_lexicon})


def test_legacy_layer_lists_return_tuple_compatible_success_and_conflict_results():
    """Frozen BDD list layers need aliases without weakening typed conflict behavior."""
    layers = [
        {
            "layer": "global",
            "version": "global-v1",
            "entry_id": "global-id",
            "spoken_form": "global form",
            "scope": "global",
        },
        {
            "layer": "episode",
            "version": "episode-v4",
            "entry_id": "episode-id",
            "spoken_form": "episode form",
            "scope": "episode",
        },
    ]

    result = resolve_pronunciation("C1", layers)

    assert isinstance(result, tuple)
    assert result.accepted is True
    assert result.selected_layer == result.selected_scope == "episode"
    assert result.selected_spoken_form == result.spoken == "episode form"
    assert result.version == result.lexicon_version == "episode-v4"
    assert result.entry_id == "episode-id"
    assert result.error is None

    conflict = resolve_pronunciation(
        "LiDAR",
        [
            {
                "layer": "project",
                "version": "project-v1",
                "entry_id": "opaque-a",
                "spoken_form": "LIE-dar",
                "normalized": "lidar",
                "scope": "project",
            },
            {
                "layer": "project",
                "version": "project-v2",
                "entry_id": "opaque-b",
                "spoken_form": "LIE-der",
                "normalized": "lidar",
                "scope": "project",
            },
        ],
    )

    assert isinstance(conflict, tuple)
    assert conflict.accepted is False
    assert "conflict" in str(conflict.error).lower()
    assert conflict.selected_layer is None
    assert conflict.selected_spoken_form is None


def test_malformed_lexicon_values_fail_closed():
    """Published lexicons must reject malformed immutable values at construction."""
    with pytest.raises(ValueError):
        PronunciationEntry("", "key", "spoken", "v1")
    with pytest.raises(ValueError):
        PronunciationEntry("id", "key", "", "v1")
    with pytest.raises(ValueError):
        PronunciationEntry("id", "key", "spoken", "v1", category="number")  # type: ignore[arg-type]
