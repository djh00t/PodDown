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
