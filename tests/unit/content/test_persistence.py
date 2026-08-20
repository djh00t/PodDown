"""Unit coverage for durable canonical preparation handoff."""

from __future__ import annotations

from pathlib import Path

import pytest

from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import ScriptTurn, SourceAnchor, VoiceAsset, VoiceConsent
from poddown.content.persistence import (
    PreparedContentPersistenceError,
    PreparedContentStore,
)
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import ContentPreparationRequest, prepare_content
from poddown.content.source import snapshot_source


def _prepared():
    source = "LiDAR remains source-bound.\n"
    snapshot = snapshot_source(source)
    block = snapshot.blocks[0]
    anchor = SourceAnchor(block.block_id, block.start, block.end)
    treatment = EpisodeTreatment(
        "treatment-1",
        "narration",
        ("source-bound",),
        1,
        ("reference",),
        {"host": "presenter"},
        (anchor,),
        ("turn-1",),
    )
    turn = ScriptTurn("turn-1", "host", source.strip(), "factual", (anchor,), (anchor,))
    request = ContentPreparationRequest(
        source,
        """profile_id: narration
version: v1
format_type: narration
target_minutes: 1
speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
""",
        treatment,
        FixtureReasoningPort(
            {snapshot.source_sha256: AdaptationProposal(treatment, (turn,))}, {}
        ),
        {
            "episode": PronunciationLexicon(
                "episode",
                "v1",
                (PronunciationEntry("lidar", "LiDAR", "LIE-dar", "v1"),),
            )
        },
        SegmentationCapabilities(100, None, frozenset({"host"})),
        (VoiceAsset("voice-host", True),),
        (VoiceConsent("voice-host", True),),
    )
    return prepare_content(request)


def test_prepared_content_round_trip_preserves_canonical_evidence(
    tmp_path: Path,
) -> None:
    result = _prepared()
    store = PreparedContentStore(tmp_path)

    store.save("a" * 64, result)
    loaded = store.load("a" * 64)

    assert loaded.snapshot == result.snapshot
    assert loaded.profile == result.profile
    assert loaded.script == result.script
    assert loaded.tokens == result.tokens
    assert loaded.segments == result.segments
    assert loaded.manifest_sha256 == result.manifest_sha256


def test_prepared_content_store_rejects_conflicting_replay(tmp_path: Path) -> None:
    store = PreparedContentStore(tmp_path)
    result = _prepared()
    store.save("b" * 64, result)
    (tmp_path / ("b" * 64 + ".json")).write_text("{}", encoding="utf-8")

    with pytest.raises(PreparedContentPersistenceError, match="conflicts"):
        store.save("b" * 64, result)
