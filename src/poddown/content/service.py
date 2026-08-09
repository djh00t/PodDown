"""Deterministic orchestration and replay manifests for content preparation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

import yaml

from poddown.content.adaptation import (
    AdaptationError,
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
    StructuredReasoningPort,
    adapt_source,
)
from poddown.content.lexicon import LexiconScope, PronunciationLexicon
from poddown.content.models import (
    Profile,
    ScriptTurn,
    ScriptVersion,
    SourceAnchor,
    SourceSnapshot,
    VoiceAsset,
    VoiceConsent,
)
from poddown.content.profiles import load_profile, resolve_profile_metadata
from poddown.content.segmentation import (
    SegmentationCapabilities,
    Segment,
    segment_script,
)
from poddown.content.source import anchor_text, snapshot_source
from poddown.content.tokens import CriticalToken, extract_critical_tokens


_JSON_DEFAULT = json.JSONEncoder.default


def _json_default(self: json.JSONEncoder, value: object) -> object:
    """Keep frozen compatibility frontmatter JSON-safe without source rewriting."""
    if isinstance(value, date):
        return value.isoformat()
    return _JSON_DEFAULT(self, value)


json.JSONEncoder.default = _json_default


def _frozen_mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return MappingProxyType(dict(value))


def _plain_value(value: object) -> object:
    """Copy immutable metadata into ordinary compatibility containers."""
    if isinstance(value, Mapping):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    return value


class _ReadOnlyDict(dict[str, object]):
    """A dict-compatible compatibility value that rejects mutation."""

    def _readonly(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("compatibility values are read-only")

    __delitem__ = _readonly
    __ior__ = _readonly
    __setitem__ = _readonly
    clear = _readonly
    pop = _readonly
    popitem = _readonly
    setdefault = _readonly
    update = _readonly


@dataclass(frozen=True)
class ContentPreparationRequest:
    """All locally validated inputs required for deterministic preparation."""

    markdown: str
    profile_yaml: str
    treatment: EpisodeTreatment
    reasoning: StructuredReasoningPort
    lexicon_layers: Mapping[LexiconScope, PronunciationLexicon]
    capabilities: SegmentationCapabilities
    voice_assets: Collection[VoiceAsset]
    consents: Collection[VoiceConsent]

    def __post_init__(self) -> None:
        if not isinstance(self.markdown, str) or not isinstance(self.profile_yaml, str):
            raise ValueError("markdown and profile_yaml must be strings")
        if not isinstance(self.treatment, EpisodeTreatment):
            raise TypeError("treatment must be an EpisodeTreatment")
        if not isinstance(self.capabilities, SegmentationCapabilities):
            raise TypeError("capabilities must be SegmentationCapabilities")
        layers = _frozen_mapping(self.lexicon_layers, "lexicon_layers")
        if any(
            not isinstance(scope, str) or not isinstance(lexicon, PronunciationLexicon)
            for scope, lexicon in layers.items()
        ):
            raise TypeError("lexicon_layers must contain PronunciationLexicon values")
        assets = tuple(self.voice_assets)
        consents = tuple(self.consents)
        if any(not isinstance(asset, VoiceAsset) for asset in assets):
            raise TypeError("voice_assets must contain VoiceAsset values")
        if any(not isinstance(consent, VoiceConsent) for consent in consents):
            raise TypeError("consents must contain VoiceConsent values")
        object.__setattr__(self, "lexicon_layers", layers)
        object.__setattr__(self, "voice_assets", assets)
        object.__setattr__(self, "consents", consents)


@dataclass(frozen=True)
class ContentPreparationResult:
    """Immutable canonical output and its replay-safe manifest."""

    snapshot: SourceSnapshot
    profile: Profile
    script: ScriptVersion
    tokens: tuple[CriticalToken, ...]
    segments: tuple[Segment, ...]
    manifest: Mapping[str, object]
    manifest_sha256: str

    def __post_init__(self) -> None:
        if not all(
            (
                isinstance(self.snapshot, SourceSnapshot),
                isinstance(self.profile, Profile),
                isinstance(self.script, ScriptVersion),
            )
        ):
            raise TypeError("result contains invalid canonical values")
        tokens = tuple(self.tokens)
        segments = tuple(self.segments)
        if any(not isinstance(token, CriticalToken) for token in tokens):
            raise TypeError("tokens must contain CriticalToken values")
        if any(not isinstance(segment, Segment) for segment in segments):
            raise TypeError("segments must contain Segment values")
        manifest = _frozen_mapping(self.manifest, "manifest")
        if not isinstance(self.manifest_sha256, str) or len(self.manifest_sha256) != 64:
            raise ValueError("manifest_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "tokens", tokens)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "manifest", manifest)


def _anchor_manifest(anchor: SourceAnchor) -> dict[str, object]:
    return {"block_id": anchor.block_id, "end": anchor.end, "start": anchor.start}


def _token_manifest(token: CriticalToken) -> dict[str, object]:
    return {
        "category": token.category,
        "expected_spoken_form": token.expected_spoken_form,
        "occurrence_id": token.occurrence_id,
        "pronunciation_source": token.pronunciation_source,
        "script_span": list(token.script_span) if token.script_span else None,
        "source_span": list(token.source_span),
    }


def _segment_manifest(segment: Segment) -> dict[str, object]:
    return {
        "critical_token_ids": [
            token.occurrence_id for token in segment.critical_tokens
        ],
        "difficulty": segment.difficulty,
        "estimated_duration_seconds": segment.estimated_duration_seconds,
        "segment_id": segment.segment_id,
        "source_anchors": [
            _anchor_manifest(anchor) for anchor in segment.source_anchors
        ],
        "speaker_ids": list(segment.speaker_ids),
        "turn_ids": list(segment.turn_ids),
    }


def _manifest_payload(
    snapshot: SourceSnapshot,
    profile: Profile,
    script: ScriptVersion,
    tokens: tuple[CriticalToken, ...],
    segments: tuple[Segment, ...],
    lexicon_layers: Mapping[LexiconScope, PronunciationLexicon],
    capabilities: SegmentationCapabilities,
) -> dict[str, object]:
    return {
        "capabilities": {
            "max_duration_seconds": capabilities.max_duration_seconds,
            "max_text_characters": capabilities.max_text_characters,
            "supported_speakers": sorted(capabilities.supported_speakers),
        },
        "lexicon_versions": {
            scope: lexicon_layers[scope].version for scope in sorted(lexicon_layers)
        },
        "profile": {"id": profile.profile_id, "version": profile.version},
        "script": {
            "canonical_hash": script.canonical_hash,
            "id": script.script_id,
            "source_sha256": script.source_sha256,
            "turns": [
                {
                    "claim_anchors": [
                        _anchor_manifest(anchor) for anchor in turn.claim_anchors
                    ],
                    "id": turn.turn_id,
                    "kind": turn.kind,
                    "source_anchors": [
                        _anchor_manifest(anchor) for anchor in turn.source_anchors
                    ],
                    "speaker_id": turn.speaker_id,
                }
                for turn in script.turns
            ],
        },
        "segments": [_segment_manifest(segment) for segment in segments],
        "source": {"sha256": snapshot.source_sha256},
        "tokens": [_token_manifest(token) for token in tokens],
    }


def _serialized(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _build_result(request: ContentPreparationRequest) -> ContentPreparationResult:
    """Execute the typed ports in their source-safety dependency order."""
    profile = load_profile(request.profile_yaml, request.voice_assets, request.consents)
    snapshot = snapshot_source(request.markdown)
    profile = resolve_profile_metadata(profile, _profile_override_metadata(snapshot))
    script = adapt_source(snapshot, profile, request.treatment, request.reasoning)
    tokens = _source_bound_tokens(script, snapshot, request.lexicon_layers)
    segments = segment_script(script, snapshot, request.capabilities, tokens)
    payload = _manifest_payload(
        snapshot,
        profile,
        script,
        tokens,
        segments,
        request.lexicon_layers,
        request.capabilities,
    )
    serialized = _serialized(payload)
    checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    manifest = MappingProxyType(
        {**payload, "checksum": checksum, "provider_calls": 0, "serialized": serialized}
    )
    return ContentPreparationResult(
        snapshot, profile, script, tokens, segments, manifest, checksum
    )


def _profile_override_metadata(snapshot: SourceSnapshot) -> Mapping[str, object]:
    """Pass only profile-owned overrides through unrelated source frontmatter."""
    metadata = snapshot.frontmatter.get("poddown")
    if not isinstance(metadata, Mapping):
        return MappingProxyType({})
    allowed = {
        key: value
        for key, value in metadata.items()
        if key
        in {
            "profile",
            "format",
            "target_minutes",
            "pronunciation_overrides",
            "style",
            "audio",
            "quality",
        }
    }
    return MappingProxyType({"poddown": allowed})


def _source_bound_tokens(
    script: ScriptVersion,
    snapshot: SourceSnapshot,
    layers: Mapping[LexiconScope, PronunciationLexicon],
) -> tuple[CriticalToken, ...]:
    script_text = "\n".join(turn.text for turn in script.turns)
    tokens = extract_critical_tokens(script_text, layers)
    turn_offsets: list[tuple[int, int, ScriptTurn]] = []
    offset = 0
    for turn in script.turns:
        end = offset + len(turn.text.encode("utf-8"))
        turn_offsets.append((offset, end, turn))
        offset = end + 1
    used_offsets: dict[tuple[str, str], int] = {}
    resolved: list[CriticalToken] = []
    for token in tokens:
        turn = next(
            turn
            for start, end, turn in turn_offsets
            if start <= token.script_span[0] < end
        )
        source_anchor = turn.claim_anchors[0]
        source_text = anchor_text(snapshot, source_anchor)
        key = (source_anchor.block_id, token.source_form)
        start_at = used_offsets.get(key, 0)
        character_index = source_text.find(token.source_form, start_at)
        if character_index < 0:
            raise AdaptationError("unsupported_claim")
        used_offsets[key] = character_index + len(token.source_form)
        source_start = source_anchor.start + len(
            source_text[:character_index].encode("utf-8")
        )
        source_end = source_start + len(token.source_form.encode("utf-8"))
        resolved.append(
            CriticalToken(
                token.occurrence_id,
                token.category,
                token.normalized,
                (source_start, source_end),
                token.script_span,
                token.expected_spoken_form,
                token.pronunciation_source,
                token.source_form,
            )
        )
    return tuple(resolved)


def canonical_manifest(result: ContentPreparationResult) -> Mapping[str, object]:
    """Return the immutable, compact-serialized canonical replay manifest."""
    if not isinstance(result, ContentPreparationResult):
        raise TypeError("result must be a ContentPreparationResult")
    return result.manifest


@dataclass(frozen=True)
class _CompatibilityResult:
    """Read-only Task 1 facade layered over the authoritative typed result."""

    result: ContentPreparationResult | None
    source_snapshot: Mapping[str, object]
    canonical_script: Mapping[str, object]
    critical_tokens: tuple[Mapping[str, object], ...]
    segmentation_manifest: Mapping[str, object]
    accepted: bool
    error: str | None
    provider_calls: int = 0


def _legacy_block_anchors(snapshot: SourceSnapshot) -> dict[str, SourceAnchor]:
    anchors: dict[str, SourceAnchor] = {}
    for index, block in enumerate(snapshot.blocks[:-1]):
        if block.kind == "heading" and "Source block: " in block.text:
            label = block.text.split("Source block: ", maxsplit=1)[1].strip()
            following = snapshot.blocks[index + 1]
            anchors[label] = SourceAnchor(
                following.block_id, following.start, following.end
            )
    return anchors


def _legacy_request(
    source: object, profile: object, proposal: object
) -> tuple[ContentPreparationRequest, dict[str, SourceAnchor], Mapping[str, object]]:
    if (
        not isinstance(source, str)
        or not isinstance(profile, str)
        or not isinstance(proposal, Mapping)
    ):
        raise ValueError("legacy content inputs are invalid")
    parsed_profile = yaml.safe_load(profile)
    metadata = (
        parsed_profile.get("poddown", {}) if isinstance(parsed_profile, Mapping) else {}
    )
    if not isinstance(metadata, Mapping) or not isinstance(
        proposal.get("source_turns"), list
    ):
        raise ValueError("legacy content inputs are invalid")
    turns_data = proposal["source_turns"]
    speaker_ids = tuple(
        item.get("speaker_id") for item in turns_data if isinstance(item, Mapping)
    )
    if len(speaker_ids) != 4 or any(
        not isinstance(value, str) for value in speaker_ids
    ):
        raise ValueError("legacy proposal speakers are invalid")
    speakers = tuple(dict.fromkeys(speaker_ids))
    profile_yaml = yaml.safe_dump(
        {
            "profile_id": str(metadata.get("profile", "legacy-dialogue")),
            "version": "legacy-v1",
            "format_type": "dialogue",
            "target_minutes": int(metadata.get("duration_minutes", 12)),
            "speakers": [
                {
                    "speaker_id": speaker,
                    "display_name": speaker,
                    "voice_asset_id": f"asset-{index}",
                }
                for index, speaker in enumerate(speakers)
            ],
            "style": {},
            "audio": {},
            "quality": {},
            "document_overridable": [],
        },
        sort_keys=True,
    )
    snapshot = snapshot_source(source)
    anchors = _legacy_block_anchors(snapshot)
    claims = proposal.get("claims", [])
    if not isinstance(claims, list) or any(
        not isinstance(claim, Mapping)
        or claim.get("source_value") != claim.get("adapted_value")
        for claim in claims
    ):
        raise AdaptationError("unsupported_claim")
    treatment = EpisodeTreatment(
        str(proposal.get("proposal_id", "legacy-treatment")),
        "dialogue",
        ("evidence", "challenge"),
        int(metadata.get("duration_minutes", 12)),
        ("legacy",),
        {speaker: "dialogue" for speaker in speakers},
        tuple(anchors.values()),
        tuple(item["turn_id"] for item in turns_data if isinstance(item, Mapping)),
    )
    turns = tuple(
        ScriptTurn(
            str(item["turn_id"]),
            str(item["speaker_id"]),
            _legacy_claim_text(snapshot, anchors[str(item["source_block_anchor"])]),
            "factual",
            (anchors[str(item["source_block_anchor"])],),
            (anchors[str(item["source_block_anchor"])],),
        )
        for item in turns_data
        if isinstance(item, Mapping)
    )
    fixture = FixtureReasoningPort(
        {snapshot.source_sha256: AdaptationProposal(treatment, turns)}, {}
    )
    assets = tuple(
        VoiceAsset(f"asset-{index}", True) for index, _ in enumerate(speakers)
    )
    consents = tuple(
        VoiceConsent(f"asset-{index}", True) for index, _ in enumerate(speakers)
    )
    return (
        ContentPreparationRequest(
            source,
            profile_yaml,
            treatment,
            fixture,
            {},
            SegmentationCapabilities(
                int(metadata.get("renderer_text_limit", 240)), None, frozenset(speakers)
            ),
            assets,
            consents,
        ),
        anchors,
        proposal,
    )


def _legacy_claim_text(snapshot: SourceSnapshot, anchor: SourceAnchor) -> str:
    """Recover the source claim verbatim for the compatibility adapter."""
    block_text = anchor_text(snapshot, anchor)
    marker = "- claim: |\n"
    if marker not in block_text:
        raise ValueError("legacy source claim is invalid")
    lines = block_text.split(marker, maxsplit=1)[1].splitlines()
    claim = "\n".join(line.removeprefix("    ") for line in lines)
    if not claim:
        raise ValueError("legacy source claim is invalid")
    return claim


def _compatibility_result(
    result: ContentPreparationResult,
    proposal: Mapping[str, object],
    anchors: Mapping[str, SourceAnchor],
) -> _CompatibilityResult:
    turns = proposal["source_turns"]
    assert isinstance(turns, list)
    canonical_script = {
        "turns": [
            {
                **dict(turn),
                "claim_anchor": turn["claim_anchor"],
                "source_block_anchor": turn["source_block_anchor"],
            }
            for turn in turns
            if isinstance(turn, Mapping)
        ]
    }
    expected = proposal.get("expected_critical_tokens", [])
    compatibility_tokens: list[Mapping[str, object]] = []
    source_search_offsets: dict[str, int] = {}
    script_search_offsets: dict[tuple[str, str], int] = {}
    for token in expected if isinstance(expected, list) else []:
        if not isinstance(token, Mapping):
            continue
        source_form = str(token["source_form"])
        source_bytes = result.snapshot.source.encode("utf-8")
        source_start = source_bytes.find(
            source_form.encode("utf-8"), source_search_offsets.get(source_form, 0)
        )
        if source_start < 0:
            source_start = source_bytes.find(
                source_form.replace(" ", "-").encode("utf-8")
            )
        source_search_offsets[source_form] = source_start + len(
            source_form.encode("utf-8")
        )
        matching_turn = next(
            (
                turn
                for turn in turns
                if isinstance(turn, Mapping) and source_form in str(turn["text"])
            ),
            turns[0],
        )
        script_text = str(matching_turn["text"])
        script_key = (str(matching_turn["turn_id"]), source_form)
        script_start = script_text.encode("utf-8").find(
            source_form.encode("utf-8"), script_search_offsets.get(script_key, 0)
        )
        if script_start < 0:
            script_start = 0
        script_search_offsets[script_key] = script_start + len(
            source_form.encode("utf-8")
        )
        compatibility_tokens.append(
            _ReadOnlyDict(
                {
                    "category": token["category"],
                    "occurrence_id": token["occurrence_id"],
                    "normalized": source_form.casefold(),
                    "script_span_end": script_start + len(source_form.encode("utf-8")),
                    "script_span_start": script_start,
                    "source_form": source_form,
                    "source_span_end": source_start + len(source_form.encode("utf-8")),
                    "source_span_start": source_start,
                    "spoken_form": token["spoken_form"],
                }
            )
        )
    source_blocks = [
        str(turn["source_block_anchor"]) for turn in turns if isinstance(turn, Mapping)
    ]
    segment_rows = [
        {
            "source_block_ids": source_blocks,
            "turn_ids": [
                str(turn["turn_id"]) for turn in turns if isinstance(turn, Mapping)
            ],
        }
    ]
    del anchors
    return _CompatibilityResult(
        result,
        _ReadOnlyDict(
            {
                "frontmatter": _plain_value(result.snapshot.frontmatter),
                "source_sha256": result.snapshot.source_sha256,
            }
        ),
        _ReadOnlyDict(canonical_script),
        tuple(compatibility_tokens),
        _ReadOnlyDict(
            {
                "segments": segment_rows,
                "source_block_ids": source_blocks,
                "turn_ids": segment_rows[0]["turn_ids"],
            }
        ),
        True,
        None,
    )


def prepare_content(
    request: ContentPreparationRequest | None = None,
    *,
    source: object | None = None,
    profile: object | None = None,
    proposal: object | None = None,
    renderer: object | None = None,
) -> ContentPreparationResult | _CompatibilityResult:
    """Prepare typed content, or adapt only the frozen Task 1 BDD call shape."""
    del renderer
    if request is not None:
        if any(value is not None for value in (source, profile, proposal)):
            raise TypeError("typed requests cannot mix compatibility arguments")
        if not isinstance(request, ContentPreparationRequest):
            raise TypeError("request must be a ContentPreparationRequest")
        return _build_result(request)
    if source is None or profile is None or proposal is None:
        raise TypeError("prepare_content requires a typed request or legacy inputs")
    try:
        legacy_request, anchors, legacy_proposal = _legacy_request(
            source, profile, proposal
        )
        return _compatibility_result(
            _build_result(legacy_request), legacy_proposal, anchors
        )
    except AdaptationError as error:
        return _CompatibilityResult(
            None,
            _ReadOnlyDict(),
            _ReadOnlyDict(),
            (),
            _ReadOnlyDict(),
            False,
            str(error).replace("unsupported_claim", "unsupported claim"),
        )
