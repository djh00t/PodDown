"""Deterministic source-bound dialogue adaptation and bounded repair."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol

from poddown.content.models import (
    Profile,
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    SourceSnapshot,
)
from poddown.content.source import anchor_text
from poddown.content.tokens import extract_critical_tokens

AdaptationErrorCode = Literal[
    "unsupported_claim",
    "missing_anchor",
    "invalid_speaker",
    "dialogue_quality",
    "duration",
]
_COMPARISONS = re.compile(
    r"\b(?:more|less|higher|lower|better|worse|faster|slower)\b", re.IGNORECASE
)
_EDITORIAL_QUESTION = re.compile(
    r"^(?:who|what|when|where|why|how|can|could|would|should|do|does|did|is|are)\b.*\?$",
    re.IGNORECASE,
)
_EDITORIAL_DIRECTIVE = re.compile(
    r"^(?:let's|let us|please|tell me|walk me through|explain|consider|imagine|"
    r"moving on|before we (?:continue|begin))\b.*[.!]?$",
    re.IGNORECASE,
)
_EDITORIAL_EXACT = frozenset(
    {
        "agreed",
        "exactly",
        "good question",
        "interesting",
        "okay",
        "right",
        "understood",
        "yes",
        "no",
        "thanks",
        "thank you",
        "welcome",
        "i agree",
        "i disagree",
        "that makes sense",
        "that's a fair point",
        "that's useful",
        "that's an important distinction",
        "thanks for joining us",
        "welcome to the show",
    }
)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
_UNSUPPORTED_WORDS = frozenset(
    {
        "the",
        "and",
        "that",
        "this",
        "with",
        "into",
        "during",
        "from",
        "earlier",
        "architecture",
        "reports",
        "controller",
        "localization",
        "platform",
        "mapping",
        "test",
        "window",
        "field",
        "pass",
        "high",
        "frequency",
        "independent",
        "render",
        "path",
        "split",
        "can",
        "does",
        "are",
        "is",
        "was",
        "it",
        "a",
        "an",
        "of",
        "to",
        "in",
        "at",
        "on",
        "for",
        "by",
        "or",
        "but",
        "not",
        "without",
        "i",
    }
)
_SAFE_ERROR_DETAILS = MappingProxyType(
    {
        "unsupported_claim": "claim is not supported by its claim anchors",
        "missing_anchor": "required source anchor is missing or invalid",
        "invalid_speaker": "speaker is not approved by the profile",
        "dialogue_quality": "dialogue structure does not meet the approved policy",
        "duration": "treatment duration or format is not approved",
    }
)


class AdaptationError(ValueError):
    """A safe, stable adaptation failure that does not include source text."""

    def __init__(self, code: AdaptationErrorCode, detail: str | None = None) -> None:
        del detail
        if code not in _SAFE_ERROR_DETAILS:
            raise ValueError("unsupported adaptation error code")
        self.code = code
        self.detail = _SAFE_ERROR_DETAILS[code]
        super().__init__(f"{code}: {self.detail}")


@dataclass(frozen=True)
class EpisodeTreatment:
    """Approved, non-renderable structure for a source-bound episode."""

    treatment_id: str
    format_type: Literal["narration", "dialogue"]
    narrative_arc: tuple[str, ...]
    target_minutes: int
    sections: tuple[str, ...]
    speaker_roles: Mapping[str, str]
    source_anchors: tuple[SourceAnchor, ...]
    expected_turn_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.treatment_id, str) or not self.treatment_id:
            raise ValueError("treatment_id must be a non-empty string")
        if self.format_type not in {"narration", "dialogue"}:
            raise ValueError("format_type must be narration or dialogue")
        if type(self.target_minutes) is not int or self.target_minutes < 1:
            raise ValueError("target_minutes must be a positive integer")
        for field_name in ("narrative_arc", "sections", "expected_turn_ids"):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"{field_name} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
            object.__setattr__(self, field_name, values)
        roles = dict(self.speaker_roles)
        if not roles or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in roles.items()
        ):
            raise ValueError("speaker_roles must map non-empty speaker IDs to roles")
        anchors = tuple(self.source_anchors)
        if not anchors or any(
            not isinstance(anchor, SourceAnchor) for anchor in anchors
        ):
            raise ValueError("source_anchors must contain SourceAnchor values")
        object.__setattr__(self, "speaker_roles", MappingProxyType(roles))
        object.__setattr__(self, "source_anchors", anchors)


@dataclass(frozen=True)
class AdaptationProposal:
    """Structured reasoning output before it becomes a canonical script."""

    treatment: EpisodeTreatment
    turns: tuple[ScriptTurn, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.treatment, EpisodeTreatment):
            raise ValueError("treatment must be an EpisodeTreatment")
        turns = tuple(self.turns)
        if not turns or any(not isinstance(turn, ScriptTurn) for turn in turns):
            raise ValueError("turns must contain at least one ScriptTurn")
        object.__setattr__(self, "turns", turns)


class StructuredReasoningPort(Protocol):
    """Boundary for schema-validated reasoning without renderer access."""

    def adapt(
        self, source: SourceSnapshot, profile: Profile, treatment: EpisodeTreatment
    ) -> AdaptationProposal: ...

    def repair(
        self, source: SourceSnapshot, profile: Profile, turn: ScriptTurn, failure: str
    ) -> ScriptTurn: ...


@dataclass(frozen=True)
class FixtureReasoningPort:
    """Fixture-only deterministic reasoning keyed by source hash and turn ID."""

    proposals: Mapping[str, AdaptationProposal]
    repairs: Mapping[str, ScriptTurn]

    def __post_init__(self) -> None:
        if not isinstance(self.proposals, Mapping) or not isinstance(
            self.repairs, Mapping
        ):
            raise TypeError("fixture mappings must be mappings")
        object.__setattr__(self, "proposals", MappingProxyType(dict(self.proposals)))
        object.__setattr__(self, "repairs", MappingProxyType(dict(self.repairs)))

    def adapt(
        self, source: SourceSnapshot, profile: Profile, treatment: EpisodeTreatment
    ) -> AdaptationProposal:
        try:
            proposal = self.proposals[source.source_sha256]
        except KeyError as error:
            raise LookupError("no fixture proposal for source hash") from error
        if proposal.treatment != treatment:
            raise LookupError("fixture proposal does not match treatment")
        return proposal

    def repair(
        self, source: SourceSnapshot, profile: Profile, turn: ScriptTurn, failure: str
    ) -> ScriptTurn:
        try:
            return self.repairs[turn.turn_id]
        except KeyError as error:
            raise LookupError("no fixture repair for turn ID") from error


def _safe_anchor_text(
    source: SourceSnapshot, anchor: SourceAnchor, turn_id: str
) -> str:
    try:
        return anchor_text(source, anchor)
    except ValueError as error:
        raise AdaptationError(
            "missing_anchor", f"invalid anchor for turn {turn_id}"
        ) from error


def _token_counts(text: str) -> Counter[tuple[str, str]]:
    return Counter(
        (token.category, token.normalized) for token in extract_critical_tokens(text)
    )


def _is_allowed_editorial_utterance(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    bare = normalized.rstrip(".!?")
    return (
        bare in _EDITORIAL_EXACT
        or bool(_EDITORIAL_QUESTION.fullmatch(normalized))
        or bool(_EDITORIAL_DIRECTIVE.fullmatch(normalized))
    )


def _assert_source_bound(turn: ScriptTurn, source: SourceSnapshot) -> None:
    if turn.kind == "editorial":
        if turn.claim_anchors:
            raise AdaptationError(
                "missing_anchor", f"editorial turn {turn.turn_id} has claim anchors"
            )
        if extract_critical_tokens(turn.text):
            raise AdaptationError(
                "unsupported_claim",
                f"editorial turn {turn.turn_id} contains factual literals",
            )
        if not _is_allowed_editorial_utterance(turn.text):
            raise AdaptationError(
                "unsupported_claim",
                f"editorial turn {turn.turn_id} is not a defined non-factual utterance",
            )
        return
    if not turn.source_anchors or not turn.claim_anchors:
        raise AdaptationError(
            "missing_anchor", f"factual turn {turn.turn_id} lacks required anchors"
        )
    for anchor in turn.source_anchors:
        _safe_anchor_text(source, anchor, turn.turn_id)
    claim_text = "\n".join(
        _safe_anchor_text(source, anchor, turn.turn_id) for anchor in turn.claim_anchors
    )
    source_tokens = _token_counts(claim_text)
    script_tokens = _token_counts(turn.text)
    for token, count in script_tokens.items():
        if source_tokens[token] < count:
            raise AdaptationError(
                "unsupported_claim",
                f"unsupported critical literal in turn {turn.turn_id}",
            )
    if sum(
        count
        for (category, _), count in script_tokens.items()
        if category == "negation"
    ) != sum(
        count
        for (category, _), count in source_tokens.items()
        if category == "negation"
    ):
        raise AdaptationError(
            "unsupported_claim", f"changed negation in turn {turn.turn_id}"
        )
    claim_words = {word.casefold() for word in _WORD.findall(claim_text)}
    for word in _WORD.findall(turn.text):
        normalized = word.casefold()
        if normalized not in _UNSUPPORTED_WORDS and normalized not in claim_words:
            raise AdaptationError(
                "unsupported_claim",
                f"unsupported claim language in turn {turn.turn_id}",
            )
    if _COMPARISONS.search(turn.text) and not _COMPARISONS.search(claim_text):
        raise AdaptationError(
            "unsupported_claim", f"unsupported comparison in turn {turn.turn_id}"
        )


def _validate_turns(
    source: SourceSnapshot,
    profile: Profile,
    turns: tuple[ScriptTurn, ...],
    expected_turn_ids: tuple[str, ...] = (),
) -> None:
    ids = tuple(turn.turn_id for turn in turns)
    if len(ids) != len(set(ids)) or (expected_turn_ids and ids != expected_turn_ids):
        raise AdaptationError("dialogue_quality", "turn IDs are not canonical")
    allowed_speakers = {speaker.speaker_id for speaker in profile.speakers}
    for turn in turns:
        if turn.speaker_id not in allowed_speakers:
            raise AdaptationError(
                "invalid_speaker", f"unapproved speaker in turn {turn.turn_id}"
            )
        _assert_source_bound(turn, source)
    if profile.format_type != "dialogue":
        return
    speakers = {turn.speaker_id for turn in turns}
    if len(speakers) != 2:
        raise AdaptationError(
            "dialogue_quality", "dialogue requires exactly two speakers"
        )
    if not any(
        re.search(r"\b(?:disagree|challenge|however)\b", turn.text, re.IGNORECASE)
        for turn in turns
    ):
        raise AdaptationError(
            "dialogue_quality", "dialogue requires a source-bound challenge"
        )


def _canonical_hash(
    source: SourceSnapshot, profile: Profile, turns: tuple[ScriptTurn, ...]
) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "source_sha256": source.source_sha256,
        "turns": [
            {
                "claim_anchors": [
                    (anchor.block_id, anchor.start, anchor.end)
                    for anchor in turn.claim_anchors
                ],
                "kind": turn.kind,
                "source_anchors": [
                    (anchor.block_id, anchor.start, anchor.end)
                    for anchor in turn.source_anchors
                ],
                "speaker_id": turn.speaker_id,
                "text": turn.text,
                "turn_id": turn.turn_id,
            }
            for turn in turns
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def adapt_source(
    source: SourceSnapshot,
    profile: Profile,
    treatment: EpisodeTreatment,
    reasoning: StructuredReasoningPort,
) -> ScriptVersion:
    """Adapt a fixture proposal only after all source and dialogue gates pass."""
    if (
        treatment.format_type != profile.format_type
        or treatment.target_minutes != profile.target_minutes
    ):
        raise AdaptationError(
            "duration", "treatment duration or format is not approved by profile"
        )
    for anchor in treatment.source_anchors:
        _safe_anchor_text(source, anchor, "treatment")
    proposal = reasoning.adapt(source, profile, treatment)
    if proposal.treatment != treatment:
        raise AdaptationError(
            "dialogue_quality", "proposal treatment does not match request"
        )
    _validate_turns(source, profile, proposal.turns, treatment.expected_turn_ids)
    canonical_hash = _canonical_hash(source, profile, proposal.turns)
    return ScriptVersion(
        f"script-{canonical_hash[:12]}",
        source.source_sha256,
        profile.profile_id,
        proposal.turns,
        canonical_hash,
    )


def repair_turn(
    script: ScriptVersion,
    turn_id: str,
    replacement: ScriptTurn,
    source: SourceSnapshot,
    profile: Profile,
) -> ScriptVersion:
    """Replace one rejected turn while preserving every accepted script boundary."""
    if (
        script.source_sha256 != source.source_sha256
        or script.profile_id != profile.profile_id
    ):
        raise AdaptationError(
            "dialogue_quality", "script does not match source or profile"
        )
    index = next(
        (index for index, turn in enumerate(script.turns) if turn.turn_id == turn_id),
        None,
    )
    if index is None:
        raise AdaptationError("dialogue_quality", "repair turn is not in script")
    original = script.turns[index]
    if (
        replacement.turn_id != original.turn_id
        or replacement.speaker_id != original.speaker_id
        or replacement.kind != original.kind
        or replacement.source_anchors != original.source_anchors
        or replacement.claim_anchors != original.claim_anchors
    ):
        raise AdaptationError(
            "missing_anchor", "repair changes protected turn identity or anchors"
        )
    turns = (*script.turns[:index], replacement, *script.turns[index + 1 :])
    _validate_turns(
        source, profile, turns, tuple(turn.turn_id for turn in script.turns)
    )
    canonical_hash = _canonical_hash(source, profile, turns)
    return ScriptVersion(
        f"script-{canonical_hash[:12]}",
        source.source_sha256,
        profile.profile_id,
        turns,
        canonical_hash,
    )
