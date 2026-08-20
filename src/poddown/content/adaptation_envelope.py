"""Strict source-bound adaptation response values and parser."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Literal

from poddown.content.models import ScriptTurn, SourceAnchor

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "source_sha256",
        "treatment_id",
        "model",
        "request_id",
        "turns",
        "usage",
        "estimated_cost",
    }
)
_TURN_FIELDS = frozenset(
    {"turn_id", "speaker_id", "kind", "text", "source_anchors", "claim_anchors"}
)
_ANCHOR_FIELDS = frozenset({"block_id", "start", "end"})


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _hash(name: str, value: object) -> str:
    value = _text(name, value)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _fields(value: Mapping[str, object], name: str, expected: frozenset[str]) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields do not match the adaptation contract")


@dataclass(frozen=True, slots=True)
class AdaptationUsage:
    """Non-negative provider usage counters."""

    values: Mapping[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping) or not self.values:
            raise ValueError("usage must be a non-empty object")
        normalized = dict(self.values)
        for key, value in normalized.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("usage keys must be non-empty strings")
            if type(value) is not int or value < 0:
                raise ValueError(f"usage.{key} must be a non-negative integer")
        object.__setattr__(self, "values", MappingProxyType(normalized))

    def to_record(self) -> dict[str, int]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class AdaptedTurn:
    """One provider-produced turn with immutable source/claim anchors."""

    turn_id: str
    speaker_id: str
    kind: Literal["factual", "editorial"]
    text: str
    source_anchors: tuple[SourceAnchor, ...]
    claim_anchors: tuple[SourceAnchor, ...]

    def __post_init__(self) -> None:
        _text("turn_id", self.turn_id)
        _text("speaker_id", self.speaker_id)
        _text("text", self.text)
        if self.kind not in {"factual", "editorial"}:
            raise ValueError("turn kind is unsupported")
        for name in ("source_anchors", "claim_anchors"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not all(
                isinstance(anchor, SourceAnchor) for anchor in value
            ):
                raise ValueError(f"{name} must contain SourceAnchor values")

    def to_record(self) -> dict[str, object]:
        def anchor_record(anchor: SourceAnchor) -> dict[str, object]:
            return {
                "block_id": anchor.block_id,
                "start": anchor.start,
                "end": anchor.end,
            }

        return {
            "turn_id": self.turn_id,
            "speaker_id": self.speaker_id,
            "kind": self.kind,
            "text": self.text,
            "source_anchors": [anchor_record(anchor) for anchor in self.source_anchors],
            "claim_anchors": [anchor_record(anchor) for anchor in self.claim_anchors],
        }

    def to_script_turn(self) -> ScriptTurn:
        return ScriptTurn(
            turn_id=self.turn_id,
            speaker_id=self.speaker_id,
            text=self.text,
            kind=self.kind,
            source_anchors=self.source_anchors,
            claim_anchors=self.claim_anchors,
        )


@dataclass(frozen=True, slots=True)
class AdaptationEnvelope:
    """Immutable schema-versioned adaptation output."""

    source_sha256: str
    treatment_id: str
    model: str
    request_id: str
    turns: tuple[AdaptedTurn, ...]
    usage: AdaptationUsage
    estimated_cost: Decimal

    def __post_init__(self) -> None:
        _hash("source_sha256", self.source_sha256)
        _text("treatment_id", self.treatment_id)
        _text("model", self.model)
        _text("request_id", self.request_id)
        if isinstance(self.turns, str) or not isinstance(self.turns, tuple | list):
            raise ValueError("turns must be an array of AdaptedTurn values")
        turns = tuple(self.turns)
        if not all(isinstance(turn, AdaptedTurn) for turn in turns):
            raise ValueError("turns must contain AdaptedTurn values")
        if len({turn.turn_id for turn in turns}) != len(turns):
            raise ValueError("turn IDs must be unique")
        if not isinstance(self.usage, AdaptationUsage):
            raise ValueError("usage must be AdaptationUsage")
        if (
            not isinstance(self.estimated_cost, Decimal)
            or not self.estimated_cost.is_finite()
            or self.estimated_cost < 0
            or _DECIMAL.fullmatch(format(self.estimated_cost, "f")) is None
        ):
            raise ValueError("estimated_cost must be a finite Decimal")
        object.__setattr__(self, "turns", turns)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "source_sha256": self.source_sha256,
            "treatment_id": self.treatment_id,
            "model": self.model,
            "request_id": self.request_id,
            "turns": [turn.to_record() for turn in self.turns],
            "usage": self.usage.to_record(),
            "estimated_cost": format(self.estimated_cost, "f"),
        }


def _anchor(name: str, value: object) -> SourceAnchor:
    if not isinstance(value, Mapping) or set(value) != _ANCHOR_FIELDS:
        raise ValueError(f"{name} is invalid")
    try:
        return SourceAnchor(
            block_id=_text("anchor block_id", value["block_id"]),
            start=value["start"],
            end=value["end"],
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} is invalid") from error


def _anchors(name: str, value: object) -> tuple[SourceAnchor, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return tuple(_anchor(name, item) for item in value)


def _turn(value: object) -> AdaptedTurn:
    if not isinstance(value, Mapping):
        raise ValueError("turn must be an object")
    _fields(value, "turn", _TURN_FIELDS)
    return AdaptedTurn(
        turn_id=_text("turn_id", value["turn_id"]),
        speaker_id=_text("speaker_id", value["speaker_id"]),
        kind=value["kind"],
        text=_text("text", value["text"]),
        source_anchors=_anchors("source_anchors", value["source_anchors"]),
        claim_anchors=_anchors("claim_anchors", value["claim_anchors"]),
    )


def _cost(value: object) -> Decimal:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ValueError("estimated_cost must be a Decimal string")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError("estimated_cost is invalid") from error


def parse_adaptation_envelope(record: object) -> AdaptationEnvelope:
    """Parse a strict JSON-shaped adaptation envelope."""
    if not isinstance(record, Mapping):
        raise ValueError("adaptation envelope must be an object")
    _fields(record, "adaptation envelope", _ENVELOPE_FIELDS)
    if record["schema_version"] != "1.0":
        raise ValueError("schema_version must be '1.0'")
    turns = record["turns"]
    if not isinstance(turns, list):
        raise ValueError("turns must be an array")
    usage = record["usage"]
    if not isinstance(usage, Mapping):
        raise ValueError("usage must be an object")
    return AdaptationEnvelope(
        source_sha256=_hash("source_sha256", record["source_sha256"]),
        treatment_id=_text("treatment_id", record["treatment_id"]),
        model=_text("model", record["model"]),
        request_id=_text("request_id", record["request_id"]),
        turns=tuple(_turn(item) for item in turns),
        usage=AdaptationUsage(dict(usage)),
        estimated_cost=_cost(record["estimated_cost"]),
    )


__all__ = [
    "AdaptationEnvelope",
    "AdaptationUsage",
    "AdaptedTurn",
    "parse_adaptation_envelope",
]
