"""Tests for immutable Markdown source snapshots and anchors."""

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from poddown.content.models import (
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    SourceBlock,
    SourceSnapshot,
)
from poddown.content.source import anchor_text, snapshot_source

SOURCE = """---
title: Café
custom:
  audience: technical
---
# Héading

Paragraph é.

- one
- two

> A quote.

| A | B |
|---|---|
| 1 | 2 |

```python
value = 'é'
```
"""

CRLF_COMMONMARK_SOURCE = (
    "---\r\n"
    "poddown:\r\n"
    "  format: dialogue\r\n"
    "custom:\r\n"
    "  label: café\r\n"
    "---\r\n"
    "  # Indented heading\r\n"
    "\r\n"
    "Setext title\r\n"
    "===\r\n"
    "\r\n"
    "````python\r\n"
    "inside\r\n"
    "```\r\n"
    "still fenced\r\n"
    "````\r\n"
)


def test_snapshot_preserves_original_utf8_source_and_indexes_blocks():
    """Changing a source byte or dropping Markdown syntax breaks fidelity."""
    snapshot = snapshot_source(SOURCE)

    assert snapshot.source == SOURCE
    assert (
        snapshot.source_sha256
        == "a1658a602ea260d4afd674ed9e6a240946852079b1b4b3e6b21b4d773bd83241"
    )
    assert snapshot.frontmatter == {
        "title": "Café",
        "custom": {"audience": "technical"},
    }
    assert [block.kind for block in snapshot.blocks] == [
        "heading",
        "paragraph",
        "list",
        "blockquote",
        "table",
        "code",
    ]
    assert [block.text for block in snapshot.blocks] == [
        "# Héading",
        "Paragraph é.",
        "- one\n- two",
        "> A quote.",
        "| A | B |\n|---|---|\n| 1 | 2 |",
        "```python\nvalue = 'é'\n```",
    ]


def test_block_ids_are_deterministic_and_change_with_content():
    """A changed block must not retain an ID that identifies old content."""
    first = snapshot_source(SOURCE)
    second = snapshot_source(SOURCE)
    changed = snapshot_source(SOURCE.replace("Paragraph é.", "Paragraph changed."))

    assert [block.block_id for block in first.blocks] == [
        block.block_id for block in second.blocks
    ]
    assert first.blocks[1].block_id != changed.blocks[1].block_id


def test_anchor_text_uses_half_open_utf8_byte_offsets():
    """Treating byte offsets as character offsets corrupts non-ASCII anchors."""
    snapshot = snapshot_source(SOURCE)
    paragraph = snapshot.blocks[1]
    prefix_bytes = len(b"Paragraph ")
    anchor = SourceAnchor(
        paragraph.block_id,
        paragraph.start + prefix_bytes,
        paragraph.start + prefix_bytes + len("é".encode()),
    )

    assert anchor_text(snapshot, anchor) == "é"

    assert (
        anchor_text(
            snapshot, SourceAnchor(paragraph.block_id, paragraph.start, paragraph.start)
        )
        == ""
    )
    with pytest.raises(ValueError, match="UTF-8"):
        anchor_text(
            snapshot,
            SourceAnchor(
                paragraph.block_id,
                paragraph.start + prefix_bytes,
                paragraph.start + prefix_bytes + 1,
            ),
        )


@pytest.mark.parametrize("start, end", [(-1, 0), (1, 0), (0, 99_999)])
def test_anchor_text_rejects_unknown_and_out_of_range_spans(start, end):
    """Invalid spans must not silently point at unrelated source content."""
    snapshot = snapshot_source(SOURCE)
    heading = snapshot.blocks[0]

    with pytest.raises(ValueError):
        anchor_text(snapshot, SourceAnchor(heading.block_id, start, end))


def test_anchor_text_rejects_an_unknown_block():
    """An anchor must name an indexed source block."""
    with pytest.raises(ValueError):
        anchor_text(snapshot_source(SOURCE), SourceAnchor("unknown", 0, 1))


def test_script_values_are_frozen():
    """Mutating accepted script text would invalidate its canonical hash."""
    turn = ScriptTurn("turn-1", "speaker-1", "Hello", "editorial", (), ())
    script = ScriptVersion("script-1", "a" * 64, "profile-1", (turn,), "b" * 64)

    with pytest.raises(FrozenInstanceError):
        turn.text = "Changed"
    with pytest.raises(FrozenInstanceError):
        script.turns = ()


def test_script_anchor_and_turn_collections_are_copied():
    """Script values must not retain caller-owned mutable anchor or turn lists."""
    anchors = [SourceAnchor("block-1", 0, 0)]
    turn = ScriptTurn("turn-1", "speaker-1", "Hello", "editorial", anchors, [])
    turns = [turn]
    script = ScriptVersion("script-1", "a" * 64, "profile-1", turns, "b" * 64)

    anchors.append(SourceAnchor("block-1", 0, 1))
    turns.clear()

    assert turn.source_anchors == (SourceAnchor("block-1", 0, 0),)
    assert script.turns == (turn,)


def test_nested_source_metadata_and_block_collections_are_copied_and_frozen():
    """Frozen snapshots must not retain caller-owned mutable descendants."""
    frontmatter = {"nested": {"values": ["original"]}}
    blocks = [SourceBlock("block-1", "paragraph", "x", 0, 1)]
    snapshot = SourceSnapshot(
        "x", hashlib.sha256(b"x").hexdigest(), frontmatter, blocks
    )

    frontmatter["nested"]["values"].append("changed")
    blocks.append(SourceBlock("block-2", "paragraph", "y", 1, 2))

    assert snapshot.frontmatter["nested"]["values"] == ("original",)
    assert len(snapshot.blocks) == 1
    with pytest.raises(TypeError):
        snapshot.frontmatter["nested"]["values"] += ("changed",)


def test_frontmatter_preserves_string_keys_and_rejects_non_string_collisions():
    """Complete metadata must not be rewritten by stringifying nested keys."""
    snapshot = snapshot_source('---\ncustom:\n  "01": leading\n---\n# Heading\n')
    assert snapshot.frontmatter["custom"]["01"] == "leading"

    with pytest.raises(ValueError, match="string"):
        snapshot_source('---\ncustom:\n  1: number\n  "1": text\n---\n')


def test_crlf_frontmatter_and_commonmark_block_boundaries_are_preserved():
    """CRLF, setext/indented headings, and longer fences retain exact spans."""
    snapshot = snapshot_source(CRLF_COMMONMARK_SOURCE)

    assert snapshot.frontmatter == {
        "poddown": {"format": "dialogue"},
        "custom": {"label": "café"},
    }
    assert [block.kind for block in snapshot.blocks] == [
        "heading",
        "heading",
        "code",
    ]
    assert [block.block_id for block in snapshot.blocks] == [
        "block-0000-5381f4bdb4a5",
        "block-0001-555c0757dd3b",
        "block-0002-630771c22f62",
    ]
    assert [(block.start, block.end) for block in snapshot.blocks] == [
        (65, 85),
        (89, 106),
        (110, 153),
    ]
    assert [block.text for block in snapshot.blocks] == [
        "  # Indented heading",
        "Setext title\r\n===",
        "````python\r\ninside\r\n```\r\nstill fenced\r\n````",
    ]
