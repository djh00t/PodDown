"""Markdown source snapshots, block indexing, and anchor resolution."""

import hashlib
import re
from collections.abc import Mapping

import yaml

from poddown.content.models import (
    SourceAnchor,
    SourceBlock,
    SourceBlockKind,
    SourceSnapshot,
    freeze_mapping,
)

_HEADING = re.compile(r"^#{1,6}\s")
_LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _frontmatter(source: str) -> tuple[Mapping[str, object], int]:
    if not source.startswith("---\n"):
        return {}, 0
    boundary = source.find("\n---\n", 4)
    if boundary < 0:
        raise ValueError("Markdown frontmatter is not terminated")
    try:
        parsed = yaml.safe_load(source[4:boundary])
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML frontmatter: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError("Markdown frontmatter must be an object")
    if any(not isinstance(key, str) for key in parsed):
        raise ValueError("Markdown frontmatter keys must be strings")
    return parsed, boundary + len("\n---\n")


def _line_offsets(source: str, start: int) -> list[tuple[int, str]]:
    offset = start
    result: list[tuple[int, str]] = []
    for line in source[start:].splitlines(True):
        result.append((offset, line))
        offset += len(line)
    return result


def _kind(line: str) -> SourceBlockKind | None:
    stripped = line.rstrip("\r\n")
    if not stripped.strip():
        return None
    if _HEADING.match(stripped):
        return "heading"
    if stripped.lstrip().startswith(">"):
        return "blockquote"
    if _LIST.match(stripped):
        return "list"
    return "paragraph"


def _is_table_start(lines: list[tuple[int, str]], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index][1].rstrip("\r\n")
    separator = lines[index + 1][1].rstrip("\r\n")
    return "|" in current and bool(re.match(r"^\s*\|?\s*:?-{3,}", separator))


def _block(
    block_index: int, kind: SourceBlockKind, source: str, start: int, end: int
) -> SourceBlock:
    text = source[start:end]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return SourceBlock(
        f"block-{block_index:04d}-{digest}",
        kind,
        text,
        len(source[:start].encode("utf-8")),
        len(source[:end].encode("utf-8")),
    )


def snapshot_source(source: str) -> SourceSnapshot:
    """Preserve Markdown exactly while indexing its addressable content blocks."""
    frontmatter, body_start = _frontmatter(source)
    lines = _line_offsets(source, body_start)
    blocks: list[SourceBlock] = []
    index = 0
    while index < len(lines):
        start, line = lines[index]
        stripped = line.rstrip("\r\n")
        if not stripped.strip():
            index += 1
            continue
        fence = _FENCE.match(stripped)
        if fence:
            marker = fence.group(1)
            end_index = index + 1
            while end_index < len(lines):
                if lines[end_index][1].lstrip().startswith(marker):
                    end_index += 1
                    break
                end_index += 1
            end = lines[end_index - 1][0] + len(lines[end_index - 1][1].rstrip("\r\n"))
            blocks.append(_block(len(blocks), "code", source, start, end))
            index = end_index
            continue
        if _is_table_start(lines, index):
            end_index = index + 2
            while end_index < len(lines) and "|" in lines[end_index][1].rstrip("\r\n"):
                end_index += 1
            end = lines[end_index - 1][0] + len(lines[end_index - 1][1].rstrip("\r\n"))
            blocks.append(_block(len(blocks), "table", source, start, end))
            index = end_index
            continue
        kind = _kind(line)
        assert kind is not None
        end_index = index + 1
        if kind in {"list", "blockquote"}:
            while end_index < len(lines) and _kind(lines[end_index][1]) == kind:
                end_index += 1
        elif kind == "paragraph":
            while end_index < len(lines):
                next_line = lines[end_index][1]
                if _kind(next_line) != "paragraph" or _is_table_start(lines, end_index):
                    break
                end_index += 1
        end = lines[end_index - 1][0] + len(lines[end_index - 1][1].rstrip("\r\n"))
        blocks.append(_block(len(blocks), kind, source, start, end))
        index = end_index
    return SourceSnapshot(
        source=source,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        frontmatter=freeze_mapping(frontmatter),
        blocks=tuple(blocks),
    )


def anchor_text(snapshot: SourceSnapshot, anchor: SourceAnchor) -> str:
    """Return an anchored UTF-8 slice or reject an invalid block-local range."""
    block = next(
        (item for item in snapshot.blocks if item.block_id == anchor.block_id), None
    )
    if block is None:
        raise ValueError(f"Unknown source block: {anchor.block_id}")
    if (
        anchor.start < block.start
        or anchor.end < anchor.start
        or anchor.end > block.end
    ):
        raise ValueError("Source anchor is outside its block")
    try:
        return snapshot.source.encode("utf-8")[anchor.start : anchor.end].decode(
            "utf-8"
        )
    except UnicodeDecodeError as error:
        raise ValueError("Source anchor splits a UTF-8 character") from error
