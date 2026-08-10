"""Tests for source-bound content preparation helpers."""

import hashlib

from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import (
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    VoiceAsset,
    VoiceConsent,
)
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.source import snapshot_source


def test_source_bound_tokens_resolve_against_later_claim_anchors():
    """Catch token provenance looking only at a turn's first claim anchor."""
    from poddown.content.service import _source_bound_tokens

    source = snapshot_source("First claim.\n\nSecond claim occurred on 2026-08-10.")
    first, second = source.blocks
    first_anchor = SourceAnchor(first.block_id, first.start, first.end)
    second_anchor = SourceAnchor(second.block_id, second.start, second.end)
    turn = ScriptTurn(
        "turn-1",
        "host",
        "Second claim occurred on 2026-08-10.",
        "factual",
        (first_anchor, second_anchor),
        (first_anchor, second_anchor),
    )
    script = ScriptVersion(
        "script-1",
        source.source_sha256,
        "profile-1",
        (turn,),
        hashlib.sha256(b"script-1").hexdigest(),
    )

    (token,) = _source_bound_tokens(script, source, {})

    assert token.source_span[0] >= second.start


def test_source_bound_tokens_match_normalized_script_spelling_to_original_source():
    """Source provenance must retain source bytes despite spelling normalization."""
    from poddown.content.service import _source_bound_tokens

    source = snapshot_source("café   o'connor")
    block = source.blocks[0]
    anchor = SourceAnchor(block.block_id, block.start, block.end)
    turn = ScriptTurn(
        "turn-1", "host", "Cafe\u0301 O’Connor", "factual", (anchor,), (anchor,)
    )
    script = ScriptVersion(
        "script-1",
        source.source_sha256,
        "profile-1",
        (turn,),
        hashlib.sha256(b"script-1").hexdigest(),
    )
    layers = {
        "project": PronunciationLexicon(
            "project",
            "v1",
            (
                PronunciationEntry(
                    "oconnor", "café o'connor", "cafe oconnor", "v1", category="name"
                ),
            ),
        )
    }

    (token,) = _source_bound_tokens(script, source, layers)

    assert token.source_form == "Cafe\u0301 O’Connor"
    assert token.source_span == (block.start, block.end)


def test_source_bound_tokens_scope_repeated_occurrences_to_each_claim_anchor():
    """Offsets from one subrange must not suppress the next anchor's occurrence."""
    from poddown.content.service import _source_bound_tokens

    source = snapshot_source("7 then 7")
    block = source.blocks[0]
    first = SourceAnchor(block.block_id, block.start, block.start + 1)
    second = SourceAnchor(block.block_id, block.end - 1, block.end)
    turn = ScriptTurn(
        "turn-1", "host", "7 then 7", "factual", (first, second), (first, second)
    )
    script = ScriptVersion(
        "script-1",
        source.source_sha256,
        "profile-1",
        (turn,),
        hashlib.sha256(b"script-1").hexdigest(),
    )

    tokens = _source_bound_tokens(script, source, {})

    assert [token.source_span for token in tokens] == [(0, 1), (7, 8)]


def test_build_result_applies_frontmatter_pronunciation_overrides_over_project_layers():
    """Episode frontmatter must outrank project lexicons during token extraction."""
    from poddown.content.service import ContentPreparationRequest, _build_result

    markdown = (
        "---\npoddown:\n  pronunciation_overrides:\n    LiDAR: LYE-dar\n---\nLiDAR\n"
    )
    snapshot = snapshot_source(markdown)
    block = snapshot.blocks[0]
    anchor = SourceAnchor(block.block_id, block.start, block.end)
    treatment = EpisodeTreatment(
        "lidar-episode",
        "narration",
        ("evidence",),
        1,
        ("intro",),
        {"host": "host"},
        (anchor,),
        ("turn-1",),
    )
    proposal = AdaptationProposal(
        treatment,
        (ScriptTurn("turn-1", "host", "LiDAR", "factual", (anchor,), (anchor,)),),
    )
    request = ContentPreparationRequest(
        markdown,
        (
            "profile_id: narration\nversion: v1\nformat_type: narration\n"
            "target_minutes: 1\nspeakers:\n  - speaker_id: host\n"
            "    display_name: Host\n    voice_asset_id: voice-host\n"
        ),
        treatment,
        FixtureReasoningPort({snapshot.source_sha256: proposal}, {}),
        {
            "project": PronunciationLexicon(
                "project",
                "v1",
                (
                    PronunciationEntry(
                        "lidar", "LiDAR", "LIE-dar", "v1", category="technical_term"
                    ),
                ),
            )
        },
        SegmentationCapabilities(100, None, frozenset({"host"})),
        (VoiceAsset("voice-host", True),),
        (VoiceConsent("voice-host", True),),
    )

    result = _build_result(request)

    (token,) = result.tokens
    assert token.expected_spoken_form == "LYE-dar"
    assert token.pronunciation_source is not None
    assert token.pronunciation_source.startswith("episode:")
