"""Tests for source-bound content preparation helpers."""

import hashlib

from poddown.content.models import ScriptTurn, ScriptVersion, SourceAnchor
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
