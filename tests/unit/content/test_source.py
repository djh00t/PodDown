"""Tests for immutable Markdown source snapshots and anchors."""

from dataclasses import FrozenInstanceError

import pytest

from poddown.content.models import ScriptTurn, ScriptVersion, SourceAnchor
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
