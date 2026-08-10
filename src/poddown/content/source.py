"""Markdown source snapshots, block indexing, and anchor resolution."""

import hashlib
import re
from collections.abc import Mapping

from poddown.content.models import (
    SourceAnchor,
    SourceBlock,
    SourceBlockKind,
    SourceSnapshot,
    freeze_mapping,
)
from poddown.content.profiles import _load_yaml

_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
_LIST = re.compile(
    r"^(?P<indent> {0,3})(?P<marker>[-+*]|\d{1,9}[.)])"
    r"(?:(?P<whitespace>[ \t]+)(?P<content>\S.*)|(?P<empty>[ \t]*))$"
)
_BLOCKQUOTE = re.compile(r"^ {0,3}>")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_TAB_STOP = 4


def _frontmatter(source: str) -> tuple[Mapping[str, object], int]:
    """Parse frontmatter with either LF or CRLF line endings."""
    lines = source.splitlines(True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, 0
    first_end = len(lines[0])
    offset = first_end
    for line in lines[1:]:
        if line.rstrip("\r\n") == "---":
            parsed = _load_yaml(source[first_end:offset])
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
    if "|" not in current:
        return False
    header_cells = _table_cells(current)
    delimiter_cells = _table_cells(separator)
    return bool(
        header_cells
        and len(header_cells) == len(delimiter_cells)
        and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in delimiter_cells)
    )


def _table_cells(line: str) -> list[str]:
    """Return pipe-delimited cells without optional outer table borders."""
    cells = line.strip().split("|")
    if line.lstrip().startswith("|"):
        cells = cells[1:]
    if line.rstrip().endswith("|"):
        cells = cells[:-1]
    return cells


def _is_setext_underline(line: str) -> bool:
    return _SETEXT.fullmatch(line.rstrip("\r\n")) is not None


def _expanded_columns(text: str, start: int = 0) -> int:
    column = start
    for character in text:
        if character == " ":
            column += 1
        elif character == "\t":
            column += _TAB_STOP - (column % _TAB_STOP)
        else:
            break
    return column


def _list_match(line: str) -> re.Match[str] | None:
    return _LIST.match(line.rstrip("\r\n"))


def _list_marker_end(match: re.Match[str]) -> int:
    return _expanded_columns(match.group("indent")) + len(match.group("marker"))


def _list_whitespace_columns(match: re.Match[str]) -> int | None:
    whitespace = match.group("whitespace")
    if whitespace is None:
        return None
    marker_end = _list_marker_end(match)
    return _expanded_columns(whitespace, marker_end) - marker_end


def _list_item_has_paragraph(line: str) -> bool:
    match = _list_match(line)
    if match is None or match.group("content") is None:
        return False
    whitespace_columns = _list_whitespace_columns(match)
    return whitespace_columns is not None and whitespace_columns <= 4


def _list_interrupts_paragraph(line: str) -> bool:
    match = _list_match(line)
    if not _list_item_has_paragraph(line) or match is None:
        return False
    marker = match.group("marker")
    return not marker[0].isdigit() or int(marker[:-1]) == 1


def _list_continuation_columns(line: str) -> int | None:
    match = _list_match(line)
    if match is None:
        return None
    marker_end = _list_marker_end(match)
    whitespace_columns = _list_whitespace_columns(match)
    if whitespace_columns is None:
        return marker_end + 1
    if whitespace_columns > 4:
        return marker_end + 1
    return marker_end + whitespace_columns


def _is_list_continuation(line: str, minimum_columns: int) -> bool:
    stripped = line.rstrip("\r\n")
    return bool(stripped.strip()) and _expanded_columns(stripped) >= minimum_columns


def _is_list_paragraph_continuation(
    lines: list[tuple[int, str]], index: int, minimum_columns: int
) -> bool:
    line = lines[index][1]
    return (
        _kind(line) == "paragraph"
        and _fence_open(line) is None
        and not _is_table_start(lines, index)
        and not _is_setext_underline(line)
        and _expanded_columns(line.rstrip("\r\n")) < minimum_columns + 4
    )


def _is_blank_line(line: str) -> bool:
    return not line.rstrip("\r\n").strip()


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
        end_index = index + 1
        if kind == "list":
            continuation_columns = _list_continuation_columns(line)
            assert continuation_columns is not None
            item_has_paragraph = _list_item_has_paragraph(line)
            active_fence: tuple[str, int] | None = None
            while end_index < len(lines):
                next_line = lines[end_index][1]
                if active_fence is not None:
                    if _fence_close(next_line, *active_fence):
                        active_fence = None
                    item_has_paragraph = False
                    end_index += 1
                    continue
                next_list_columns = _list_continuation_columns(next_line)
                if next_list_columns is not None:
                    continuation_columns = next_list_columns
                    item_has_paragraph = _list_item_has_paragraph(next_line)
                    end_index += 1
                    continue
                if _is_list_continuation(next_line, continuation_columns):
                    fence = _fence_open(next_line)
                    active_fence = fence
                    item_has_paragraph = (
                        fence is None
                        and _is_list_paragraph_continuation(
                            lines, end_index, continuation_columns
                        )
                    )
                    end_index += 1
                    continue
                if _is_blank_line(next_line):
                    lookahead = end_index
                    while lookahead < len(lines) and _is_blank_line(
                        lines[lookahead][1]
                    ):
                        lookahead += 1
                    if lookahead < len(lines) and (
                        _list_continuation_columns(lines[lookahead][1]) is not None
                        or _is_list_continuation(
                            lines[lookahead][1], continuation_columns
                        )
                    ):
                        end_index += 1
                        continue
                if (
                    item_has_paragraph
                    and _kind(next_line) == "paragraph"
                    and not _is_table_start(lines, end_index)
                    and _fence_open(next_line) is None
                    and not _is_setext_underline(next_line)
                ):
                    end_index += 1
                    continue
                break
        elif kind == "blockquote":
            while end_index < len(lines) and _kind(lines[end_index][1]) == kind:
                end_index += 1
        elif kind == "paragraph":
            while end_index < len(lines):
                next_line = lines[end_index][1]
                next_kind = _kind(next_line)
                if next_kind == "list" and not _list_interrupts_paragraph(next_line):
                    end_index += 1
                    continue
                if (
                    next_kind != "paragraph"
                    or _is_table_start(lines, end_index)
                    or _fence_open(next_line)
                    or _is_setext_underline(next_line)
                ):
                    break
                end_index += 1
            if end_index < len(lines) and _is_setext_underline(lines[end_index][1]):
                end_index += 1
                kind = "heading"
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
