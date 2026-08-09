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

_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
_LIST = re.compile(r"^ {0,3}(?:[-+*]|\d+[.)])[ \t]+")
_BLOCKQUOTE = re.compile(r"^ {0,3}>")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


def _frontmatter(source: str) -> tuple[Mapping[str, object], int]:
    """Parse frontmatter with either LF or CRLF line endings."""
    lines = source.splitlines(True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, 0
    first_end = len(lines[0])
    offset = first_end
    for line in lines[1:]:
        if line.rstrip("\r\n") == "---":
            try:
                parsed = yaml.safe_load(source[first_end:offset])
            except yaml.YAMLError as error:
                raise ValueError(f"Invalid YAML frontmatter: {error}") from error
            if not isinstance(parsed, dict):
                raise ValueError("Markdown frontmatter must be an object")
            return parsed, offset + len(line)
        offset += len(line)
    raise ValueError("Markdown frontmatter is not terminated")


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
    if _BLOCKQUOTE.match(stripped):
        return "blockquote"
    if _LIST.match(stripped):
        return "list"
    return "paragraph"


def _fence_open(line: str) -> tuple[str, int] | None:
    match = _FENCE.match(line.rstrip("\r\n"))
    if match is None:
        return None
    marker = match.group(1)
    info = match.group(2)
    if marker[0] == "`" and "`" in info:
        return None
    return marker[0], len(marker)


def _fence_close(line: str, marker_char: str, marker_length: int) -> bool:
    match = _FENCE_CLOSE.match(line.rstrip("\r\n"))
    return bool(
        match is not None
        and match.group(1)[0] == marker_char
        and len(match.group(1)) >= marker_length
    )


def _is_table_start(lines: list[tuple[int, str]], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index][1].rstrip("\r\n")
    separator = lines[index + 1][1].rstrip("\r\n")
    return "|" in current and bool(re.match(r"^\s*\|?\s*:?-{3,}", separator))


def _is_setext_underline(line: str) -> bool:
    return _SETEXT.fullmatch(line.rstrip("\r\n")) is not None


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


def _line_end(lines: list[tuple[int, str]], index: int) -> int:
    return lines[index][0] + len(lines[index][1].rstrip("\r\n"))


def snapshot_source(source: str) -> SourceSnapshot:
    """Preserve Markdown exactly while indexing its addressable content blocks."""
    if not isinstance(source, str):
        raise ValueError("source must be a string")
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
        fence = _fence_open(line)
        if fence:
            marker_char, marker_length = fence
            end_index = index + 1
            while end_index < len(lines):
                if _fence_close(lines[end_index][1], marker_char, marker_length):
                    end_index += 1
                    break
                end_index += 1
            end = _line_end(lines, end_index - 1)
            blocks.append(_block(len(blocks), "code", source, start, end))
            index = end_index
            continue
        if _is_table_start(lines, index):
            end_index = index + 2
            while end_index < len(lines) and "|" in lines[end_index][1].rstrip("\r\n"):
                end_index += 1
            blocks.append(
                _block(
                    len(blocks),
                    "table",
                    source,
                    start,
                    _line_end(lines, end_index - 1),
                )
            )
            index = end_index
            continue
        kind = _kind(line)
        assert kind is not None
        is_setext = (
            kind == "paragraph"
            and index + 1 < len(lines)
            and _is_setext_underline(lines[index + 1][1])
        )
        if is_setext:
            end_index = index + 2
            blocks.append(
                _block(
                    len(blocks),
                    "heading",
                    source,
                    start,
                    _line_end(lines, end_index - 1),
                )
            )
            index = end_index
            continue
        end_index = index + 1
        if kind in {"list", "blockquote"}:
            while end_index < len(lines) and _kind(lines[end_index][1]) == kind:
                end_index += 1
        elif kind == "paragraph":
            while end_index < len(lines):
                next_line = lines[end_index][1]
                if (
                    _kind(next_line) != "paragraph"
                    or _is_table_start(lines, end_index)
                    or _fence_open(next_line)
                    or _is_setext_underline(next_line)
                ):
                    break
                end_index += 1
        blocks.append(
            _block(len(blocks), kind, source, start, _line_end(lines, end_index - 1))
        )
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
