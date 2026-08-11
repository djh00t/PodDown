"""Evidence-integrity tests for reference-demo source anchors."""

from __future__ import annotations

import json
from pathlib import Path

from poddown.content.source import snapshot_source

REFERENCE_FIXTURE = Path(__file__).parents[2] / "integrations" / "reference-demo" / "v1"


def test_reference_demo_anchors_match_snapshot_source_blocks() -> None:
    """Fixture anchors must identify the blocks containing their source evidence."""
    source = (REFERENCE_FIXTURE / "source.md").read_text(encoding="utf-8")
    adaptation = json.loads(
        (REFERENCE_FIXTURE / "adaptation.json").read_text(encoding="utf-8")
    )
    snapshot = snapshot_source(source)
    source_bytes = source.encode("utf-8")
    claims_by_anchor = {claim["claim_anchor"]: claim for claim in adaptation["claims"]}

    for claim in adaptation["claims"]:
        source_value = claim["source_value"]
        assert source_value in source
        source_start = source_bytes.index(source_value.encode("utf-8"))
        source_block = next(
            block
            for block in snapshot.blocks
            if block.start <= source_start < block.end
        )
        assert claim["source_block_anchor"] == source_block.block_id

    for source_turn in adaptation["source_turns"]:
        claim = claims_by_anchor[source_turn["claim_anchor"]]
        assert source_turn["source_block_anchor"] == claim["source_block_anchor"]
