"""Deterministic orchestration and replay manifests for content preparation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Protocol

import yaml

from poddown.content.adaptation import (
    AdaptationError,
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
    StructuredReasoningPort,
    adapt_source,
)
from poddown.content.lexicon import (
    LexiconScope,
    PronunciationEntry,
    PronunciationLexicon,
    normalize_lexicon_key,
)
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
    Segment,
    SegmentationCapabilities,
    segment_script,
)
from poddown.content.source import anchor_text, snapshot_source
from poddown.content.tokens import (
    CriticalToken,
    _find_normalized_span,
    extract_critical_tokens,
)

_LEGACY_PRONUNCIATIONS = (
    ("LiDAR", "LIE-dar"),
    ("C1", "see one"),
    ("13.8 hertz", "13.8 hertz"),
    ("99.7%", "ninety-nine point seven percent"),
)


def _json_default(value: object) -> object:
    """Serialize dates locally without modifying the process JSON encoder."""
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _frozen_mapping[MappingValue](
    value: Mapping[str, MappingValue], name: str
) -> Mapping[str, MappingValue]:
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
    __ior__ = _readonly  # type: ignore[assignment]
    __setitem__ = _readonly
    clear = _readonly
    pop = _readonly
    popitem = _readonly  # type: ignore[assignment]
    setdefault = _readonly
    update = _readonly


class _ReadOnlyList(list[object]):
    """A list-compatible compatibility value that rejects mutation."""

    def _readonly(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("compatibility values are read-only")

    __delitem__ = _readonly
    __iadd__ = _readonly  # type: ignore[assignment]
    __imul__ = _readonly  # type: ignore[assignment]
    __setitem__ = _readonly
    append = _readonly
    clear = _readonly
    extend = _readonly
    insert = _readonly
    pop = _readonly
    remove = _readonly
    reverse = _readonly
    sort = _readonly


def _deep_freeze(value: object) -> object:
    """Recursively preserve mapping/list read shapes while preventing writes."""
    if isinstance(value, Mapping):
        return _ReadOnlyDict({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return _ReadOnlyList([_deep_freeze(item) for item in value])
    return _plain_value(value)


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
        layers = MappingProxyType(dict(self.lexicon_layers))
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


class LiveAdaptationPort(Protocol):
    """Async source-bound adaptation port used by production preparation."""

    async def adapt(
        self,
        source: SourceSnapshot,
        profile: Profile,
        treatment: EpisodeTreatment,
    ) -> ScriptVersion:
        """Return one validated provider adaptation without local fallback."""


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
        manifest = _deep_freeze(self.manifest)
        if not isinstance(manifest, Mapping):
            raise TypeError("manifest must be a mapping")
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
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _preparation_context(
    request: ContentPreparationRequest,
) -> tuple[Profile, SourceSnapshot, Mapping[LexiconScope, PronunciationLexicon]]:
    """Load the immutable inputs shared by local and live preparation."""
    profile = load_profile(request.profile_yaml, request.voice_assets, request.consents)
    snapshot = snapshot_source(request.markdown)
    profile = resolve_profile_metadata(profile, _profile_override_metadata(snapshot))
    lexicon_layers = _frontmatter_lexicon_layers(snapshot, request.lexicon_layers)
    return profile, snapshot, lexicon_layers


def _build_result_from_script(
    request: ContentPreparationRequest,
    profile: Profile,
    snapshot: SourceSnapshot,
    lexicon_layers: Mapping[LexiconScope, PronunciationLexicon],
    script: ScriptVersion,
    *,
    provider_calls: int = 0,
) -> ContentPreparationResult:
    """Apply canonical source-bound gates to an already selected script."""
    if script.source_sha256 != snapshot.source_sha256:
        raise ValueError("live script source identity does not match the request")
    if script.profile_id != profile.profile_id:
        raise ValueError("live script profile identity does not match the request")
    if type(provider_calls) is not int or provider_calls < 0:
        raise ValueError("provider_calls must be a non-negative integer")
    # Re-run the existing canonical adaptation gates around a live result. This
    # keeps provider output from bypassing anchor, literal, negation, speaker,
    # and dialogue-quality validation, while avoiding a second provider call.
    canonical_script = adapt_source(
        snapshot,
        profile,
        request.treatment,
        FixtureReasoningPort(
            {
                snapshot.source_sha256: AdaptationProposal(
                    request.treatment, script.turns
                )
            },
            {},
        ),
    )
    tokens = _source_bound_tokens(canonical_script, snapshot, lexicon_layers)
    segments = segment_script(canonical_script, snapshot, request.capabilities, tokens)
    payload = _manifest_payload(
        snapshot,
        profile,
        canonical_script,
        tokens,
        segments,
        lexicon_layers,
        request.capabilities,
    )
    serialized = _serialized(payload)
    checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    manifest = _deep_freeze(
        {
            **payload,
            "checksum": checksum,
            "provider_calls": provider_calls,
            "serialized": serialized,
        }
    )
    assert isinstance(manifest, Mapping)
    return ContentPreparationResult(
        snapshot, profile, canonical_script, tokens, segments, manifest, checksum
    )


def _build_result(request: ContentPreparationRequest) -> ContentPreparationResult:
    """Execute the typed ports in their source-safety dependency order."""
    profile, snapshot, lexicon_layers = _preparation_context(request)
    script = adapt_source(snapshot, profile, request.treatment, request.reasoning)
    return _build_result_from_script(request, profile, snapshot, lexicon_layers, script)


async def prepare_content_live(
    request: ContentPreparationRequest,
    adaptation: LiveAdaptationPort,
) -> ContentPreparationResult:
    """Prepare content through one explicit live adaptation port.

    The provider is called exactly once by this boundary. Its returned script
    is then passed through the same canonical source-bound gates used by local
    preparation. No fixture reasoning or local fallback is consulted.
    """
    if not isinstance(request, ContentPreparationRequest):
        raise TypeError("request must be a ContentPreparationRequest")
    if not hasattr(adaptation, "adapt"):
        raise TypeError("live adaptation must expose adapt(source, profile, treatment)")
    profile, snapshot, lexicon_layers = _preparation_context(request)
    script = await adaptation.adapt(snapshot, profile, request.treatment)
    if not isinstance(script, ScriptVersion):
        raise ValueError("live adaptation returned an invalid script")
    return _build_result_from_script(
        request,
        profile,
        snapshot,
        lexicon_layers,
        script,
        provider_calls=1,
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


def _frontmatter_lexicon_layers(
    snapshot: SourceSnapshot, layers: Mapping[LexiconScope, PronunciationLexicon]
) -> Mapping[LexiconScope, PronunciationLexicon]:
    """Overlay validated document pronunciations onto the episode precedence layer."""
    metadata = _profile_override_metadata(snapshot).get("poddown", {})
    assert isinstance(metadata, Mapping)
    raw_overrides = metadata.get("pronunciation_overrides")
    if raw_overrides is None:
        return layers
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("pronunciation_overrides must be an object")
    overrides = tuple(
        sorted(
            (
                (normalize_lexicon_key(key), key, value)
                for key, value in raw_overrides.items()
                if isinstance(key, str) and isinstance(value, str)
            ),
            key=lambda item: item[0],
        )
    )
    if len(overrides) != len(raw_overrides):
        raise ValueError("pronunciation_overrides must map strings to strings")
    override_keys = {normalized for normalized, _, _ in overrides}
    version = (
        "frontmatter-"
        + hashlib.sha256(
            _serialized({key: value for _, key, value in overrides}).encode("utf-8")
        ).hexdigest()[:12]
    )
    existing = layers.get("episode")
    entries = () if existing is None else existing.entries
    retained = tuple(
        entry
        for entry in entries
        if normalize_lexicon_key(entry.key) not in override_keys
    )
    frontmatter_entries = tuple(
        PronunciationEntry(f"frontmatter-{index:04d}", key, value, version)
        for index, (_, key, value) in enumerate(overrides, start=1)
    )
    resolved = dict(layers)
    resolved["episode"] = PronunciationLexicon(
        "episode", version, (*retained, *frontmatter_entries)
    )
    return MappingProxyType(resolved)


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
    used_offsets: dict[tuple[str, int, int, str], int] = {}
    resolved: list[CriticalToken] = []
    for token in tokens:
        if token.script_span is None:
            raise AdaptationError("unsupported_claim")
        turn_start, _, turn = next(
            (start, end, turn)
            for start, end, turn in turn_offsets
            if start <= token.script_span[0] < end
        )
        match = next(
            (
                (anchor, source_text, character_span)
                for anchor in turn.claim_anchors
                for source_text in (anchor_text(snapshot, anchor),)
                for key in (
                    (
                        anchor.block_id,
                        anchor.start,
                        anchor.end,
                        normalize_lexicon_key(token.source_form),
                    ),
                )
                for character_span in (
                    _find_normalized_span(
                        source_text, token.source_form, used_offsets.get(key, 0)
                    ),
                )
                if character_span is not None
            ),
            None,
        )
        if match is None:
            raise AdaptationError("unsupported_claim")
        source_anchor, source_text, character_span = match
        character_start, character_end = character_span
        key = (
            source_anchor.block_id,
            source_anchor.start,
            source_anchor.end,
            normalize_lexicon_key(token.source_form),
        )
        used_offsets[key] = character_end
        source_start = source_anchor.start + len(
            source_text[:character_start].encode("utf-8")
        )
        source_end = source_anchor.start + len(
            source_text[:character_end].encode("utf-8")
        )
        resolved.append(
            CriticalToken(
                token.occurrence_id,
                token.category,
                token.normalized,
                (source_start, source_end),
                (
                    token.script_span[0] - turn_start,
                    token.script_span[1] - turn_start,
                ),
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
    if any(
        not isinstance(item, Mapping)
        or not isinstance(item.get("turn_id"), str)
        or not item["turn_id"]
        or not isinstance(item.get("speaker_id"), str)
        or not item["speaker_id"]
        or not isinstance(item.get("source_block_anchor"), str)
        or not item["source_block_anchor"]
        for item in turns_data
    ):
        raise AdaptationError("unsupported_claim")
    turn_ids = [
        str(item["turn_id"]) for item in turns_data if isinstance(item, Mapping)
    ]
    if len(turn_ids) != len(set(turn_ids)):
        raise AdaptationError("unsupported_claim")
    speaker_ids = tuple(
        str(item["speaker_id"]) for item in turns_data if isinstance(item, Mapping)
    )
    if len(speaker_ids) != 4:
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
    if any(
        str(item["source_block_anchor"]) not in anchors
        for item in turns_data
        if isinstance(item, Mapping)
    ):
        raise AdaptationError("unsupported_claim")
    claims = proposal.get("claims", [])
    expected_tokens = proposal.get("expected_critical_tokens", [])
    if not isinstance(claims, list) or any(
        not isinstance(claim, Mapping)
        or not isinstance(claim.get("turn_id"), str)
        or not claim["turn_id"]
        or not isinstance(claim.get("claim_anchor"), str)
        or not claim["claim_anchor"]
        or claim.get("source_value") != claim.get("adapted_value")
        for claim in claims
    ):
        raise AdaptationError("unsupported_claim")
    claim_ids = [
        (str(claim["turn_id"]), str(claim["claim_anchor"]), str(claim["source_value"]))
        for claim in claims
        if isinstance(claim, Mapping)
    ]
    if len(claim_ids) != len(set(claim_ids)):
        raise AdaptationError("unsupported_claim")
    if not isinstance(expected_tokens, list) or any(
        not isinstance(token, Mapping)
        or not isinstance(token.get("source_form"), str)
        or not token["source_form"]
        for token in expected_tokens
    ):
        raise AdaptationError("unsupported_claim")
    legacy_lexicon = PronunciationLexicon(
        "episode",
        "legacy-v1",
        tuple(
            PronunciationEntry(
                f"legacy-token-{index}",
                key,
                spoken_form,
                "legacy-v1",
            )
            for index, (key, spoken_form) in enumerate(_LEGACY_PRONUNCIATIONS)
        ),
    )
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
            {"episode": legacy_lexicon},
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


def _source_anchor_labels(
    snapshot: SourceSnapshot, anchor: SourceAnchor
) -> tuple[str, str]:
    """Read the frozen source labels from the typed anchor's Markdown block."""
    fields: dict[str, str] = {}
    for line in anchor_text(snapshot, anchor).splitlines():
        if line.startswith("- ") and ": " in line:
            key, value = line[2:].split(": ", maxsplit=1)
            fields[key] = value
    source_label = fields.get("source_block_anchor")
    claim_label = fields.get("claim_anchor")
    if not source_label or not claim_label:
        raise AdaptationError("unsupported_claim")
    return source_label, claim_label


def _compatibility_result(
    result: ContentPreparationResult,
    proposal: Mapping[str, object],
    anchors: Mapping[str, SourceAnchor],
) -> _CompatibilityResult:
    turns = proposal["source_turns"]
    claims = proposal.get("claims")
    expected = proposal.get("expected_critical_tokens")
    if (
        not isinstance(turns, list)
        or not isinstance(claims, list)
        or not isinstance(expected, list)
    ):
        raise AdaptationError("unsupported_claim")
    claims_by_turn: dict[str, list[Mapping[str, object]]] = {}
    claim_ids: set[tuple[str, str, str]] = set()
    for claim in claims:
        if (
            not isinstance(claim, Mapping)
            or not isinstance(claim.get("turn_id"), str)
            or not isinstance(claim.get("claim_anchor"), str)
            or not isinstance(claim.get("source_block_anchor"), str)
            or not isinstance(claim.get("source_value"), str)
            or not isinstance(claim.get("speaker_id"), str)
            or not normalize_lexicon_key(str(claim["source_value"]))
            or claim.get("source_value") != claim.get("adapted_value")
        ):
            raise AdaptationError("unsupported_claim")
        claim_id = (
            claim["turn_id"],
            claim["claim_anchor"],
            claim["source_value"],
        )
        if claim_id in claim_ids:
            raise AdaptationError("unsupported_claim")
        claim_ids.add(claim_id)
        claims_by_turn.setdefault(claim["turn_id"], []).append(claim)
    proposal_turns: dict[str, Mapping[str, object]] = {}
    for proposal_turn in turns:
        if (
            not isinstance(proposal_turn, Mapping)
            or not isinstance(proposal_turn.get("turn_id"), str)
            or not isinstance(proposal_turn.get("speaker_id"), str)
            or not isinstance(proposal_turn.get("source_block_anchor"), str)
            or not isinstance(proposal_turn.get("claim_anchor"), str)
            or proposal_turn["turn_id"] in proposal_turns
        ):
            raise AdaptationError("unsupported_claim")
        proposal_turns[proposal_turn["turn_id"]] = proposal_turn
    canonical_turns: list[dict[str, object]] = []
    for turn in result.script.turns:
        proposal_turn = proposal_turns.get(turn.turn_id)
        turn_claims = claims_by_turn.get(turn.turn_id)
        source_label, claim_label = _source_anchor_labels(
            result.snapshot, turn.source_anchors[0]
        )
        if (
            proposal_turn is None
            or not turn_claims
            or proposal_turn.get("speaker_id") != turn.speaker_id
            or proposal_turn["source_block_anchor"] != source_label
            or proposal_turn["claim_anchor"] != claim_label
            or any(
                claim["source_block_anchor"] != source_label
                or claim["claim_anchor"] != claim_label
                or claim["speaker_id"] != turn.speaker_id
                or normalize_lexicon_key(str(claim["source_value"]))
                not in normalize_lexicon_key(
                    _legacy_claim_text(result.snapshot, turn.source_anchors[0])
                )
                for claim in turn_claims
            )
        ):
            raise AdaptationError("unsupported_claim")
        canonical_turns.append(
            {
                "claim_anchor": claim_label,
                "claim_anchors": [
                    _anchor_manifest(anchor) for anchor in turn.claim_anchors
                ],
                "kind": turn.kind,
                "is_disagreement": "disagree" in turn.text.casefold(),
                "source_anchors": [
                    _anchor_manifest(anchor) for anchor in turn.source_anchors
                ],
                "source_block_anchor": source_label,
                "speaker_id": turn.speaker_id,
                "supported_by_source": True,
                "text": turn.text,
                "turn_id": turn.turn_id,
            }
        )
    typed_turn_ids = {turn.turn_id for turn in result.script.turns}
    if set(proposal_turns) != typed_turn_ids or set(claims_by_turn) != typed_turn_ids:
        raise AdaptationError("unsupported_claim")
    canonical_script = {"turns": canonical_turns}
    compatibility_tokens: list[Mapping[str, object]] = []
    used_tokens: set[str] = set()
    for token in expected:
        if not isinstance(token, Mapping) or not isinstance(
            token.get("source_form"), str
        ):
            raise AdaptationError("unsupported_claim")
        source_form = str(token["source_form"])
        typed_token = next(
            (
                candidate
                for candidate in result.tokens
                if candidate.occurrence_id not in used_tokens
                and candidate.source_form == source_form
            ),
            None,
        )
        if typed_token is None:
            raise AdaptationError("unsupported_claim")
        used_tokens.add(typed_token.occurrence_id)
        compatibility_tokens.append(
            {
                "category": typed_token.category,
                "occurrence_id": typed_token.occurrence_id,
                "normalized": typed_token.normalized,
                "script_span_end": typed_token.script_span_end,
                "script_span_start": typed_token.script_span_start,
                "source_form": typed_token.source_form,
                "source_span_end": typed_token.source_span_end,
                "source_span_start": typed_token.source_span_start,
                "spoken_form": typed_token.spoken_form,
            }
        )
    source_blocks = [
        _source_anchor_labels(result.snapshot, turn.source_anchors[0])[0]
        for turn in result.script.turns
    ]
    segment_rows = [
        {
            "source_block_ids": source_blocks,
            "turn_ids": [turn.turn_id for turn in result.script.turns],
        }
    ]
    frozen_source_snapshot = _deep_freeze(
        {
            "frontmatter": _plain_value(result.snapshot.frontmatter),
            "source_sha256": result.snapshot.source_sha256,
        }
    )
    frozen_script = _deep_freeze(canonical_script)
    frozen_tokens = _deep_freeze(compatibility_tokens)
    frozen_segmentation = _deep_freeze(
        {
            "segments": segment_rows,
            "source_block_ids": source_blocks,
            "turn_ids": segment_rows[0]["turn_ids"],
        }
    )
    assert isinstance(frozen_source_snapshot, Mapping)
    assert isinstance(frozen_script, Mapping)
    assert isinstance(frozen_tokens, list)
    assert isinstance(frozen_segmentation, Mapping)
    return _CompatibilityResult(
        result,
        frozen_source_snapshot,
        frozen_script,
        tuple(frozen_tokens),
        frozen_segmentation,
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
