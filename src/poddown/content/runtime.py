"""Configured source-bound content for the worker-owned production runtime."""

from __future__ import annotations

import hashlib
import json
import platform
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, cast

import yaml

from poddown.audio.rights import VoiceConsent as AudioVoiceConsent
from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import (
    LexiconScope,
    PronunciationEntry,
    PronunciationLexicon,
)
from poddown.content.models import (
    Profile,
    ScriptTurn,
    SourceAnchor,
    SourceSnapshot,
    VoiceAsset,
    VoiceConsent,
)
from poddown.content.profiles import load_profile
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import ContentPreparationRequest
from poddown.content.source import snapshot_source
from poddown.episode_service import EpisodeRecord
from poddown.workflow_snapshots import (
    CommandName,
    EpisodeWorkflowSnapshot,
    WorkflowRenderBinding,
    WorkflowSnapshotError,
    WorkflowSnapshotFactory,
    prepared_content_key_for,
)


class _StrictLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate keys in configured content."""


def _construct_mapping(
    loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)

ConfiguredRuntimeMode = Literal["deterministic-local", "host-local", "live-provider"]
_LOCAL_BINDINGS: dict[ConfiguredRuntimeMode, tuple[str, str]] = {
    "deterministic-local": ("local", "local-deterministic-v1"),
    "host-local": ("host-local", "host-local-tts-v1"),
}


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowSnapshotError(f"configured content field is invalid: {name}")
    return value


def _strings(name: str, value: object, default: list[str]) -> tuple[str, ...]:
    selected = default if value is None else value
    if not isinstance(selected, list) or not all(
        isinstance(item, str) and item for item in selected
    ):
        raise WorkflowSnapshotError(f"configured content field is invalid: {name}")
    return tuple(selected)


def _positive_int(name: str, value: object, default: int) -> int:
    selected = default if value is None else value
    if type(selected) is not int or selected < 1:
        raise WorkflowSnapshotError(f"configured content field is invalid: {name}")
    return selected


def _mapping(path: Path, text: str) -> Mapping[str, object]:
    try:
        value = (
            yaml.load(text, Loader=_StrictLoader)
            if path.suffix in {".yaml", ".yml"}
            else json.loads(text)
        )
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise WorkflowSnapshotError(
            f"configured content is malformed: {path.name}"
        ) from error
    if not isinstance(value, Mapping):
        raise WorkflowSnapshotError(
            f"configured content must be an object: {path.name}"
        )
    return cast(Mapping[str, object], value)


def _read(root: Path, name: str, *, optional: bool = False) -> tuple[Path, str] | None:
    path = root / name
    if not path.exists():
        if optional:
            return None
        raise WorkflowSnapshotError(f"configured content file is missing: {name}")
    if not path.is_file() or path.is_symlink():
        raise WorkflowSnapshotError(f"configured content file is unsafe: {name}")
    try:
        return path, path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WorkflowSnapshotError(
            f"configured content file is unreadable: {name}"
        ) from error


def _anchor_for_text(source: SourceSnapshot, text: str) -> SourceAnchor:
    encoded = text.encode("utf-8")
    try:
        start = source.source.encode("utf-8").index(encoded)
        block = next(item for item in source.blocks if item.start <= start < item.end)
    except (ValueError, StopIteration) as error:
        raise WorkflowSnapshotError(
            "configured adaptation is not source-bound"
        ) from error
    return SourceAnchor(block.block_id, block.start, block.end)


def _anchor_for_block(source: SourceSnapshot, block_label: str) -> SourceAnchor:
    label = _text("source_block_anchor", block_label)
    marker = f"Source block: {label}"
    for index, block in enumerate(source.blocks[:-1]):
        if marker in block.text:
            following = source.blocks[index + 1]
            return SourceAnchor(following.block_id, following.start, following.end)
    raise WorkflowSnapshotError("configured treatment source block is unavailable")


def _voices(
    voices: Mapping[str, object],
) -> tuple[
    tuple[VoiceAsset, ...],
    tuple[VoiceConsent, ...],
    dict[str, AudioVoiceConsent],
    dict[str, str],
]:
    raw_assets = voices.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise WorkflowSnapshotError("configured voices assets are invalid")
    content_assets: list[VoiceAsset] = []
    content_consents: list[VoiceConsent] = []
    audio_consents: dict[str, AudioVoiceConsent] = {}
    local_voice_names: dict[str, str] = {}
    for item in raw_assets:
        if not isinstance(item, Mapping):
            raise WorkflowSnapshotError("configured voice asset is invalid")
        asset_id = _text("asset_id", item.get("asset_id"))
        consent_id = _text("consent_id", item.get("consent_id"))
        if item.get("consent_status") != "approved":
            raise WorkflowSnapshotError("configured voice consent is not approved")
        allowed = item.get("allowed_providers", ["local", "host-local"])
        if (
            not isinstance(allowed, list)
            or not allowed
            or not all(isinstance(provider, str) and provider for provider in allowed)
        ):
            raise WorkflowSnapshotError(
                "configured voice provider allow-list is invalid"
            )
        content_assets.append(VoiceAsset(asset_id, True))
        content_consents.append(VoiceConsent(asset_id, True))
        audio_consents[asset_id] = AudioVoiceConsent(
            asset_id, consent_id, frozenset(allowed)
        )
        local_voice = item.get("local_voice", {})
        if not isinstance(local_voice, Mapping):
            raise WorkflowSnapshotError("configured local voice binding is invalid")
        voice_key = "macos_say_name" if platform.system() == "Darwin" else "espeak_name"
        local_voice_names[asset_id] = _text(voice_key, local_voice.get(voice_key))
    return (
        tuple(content_assets),
        tuple(content_consents),
        audio_consents,
        local_voice_names,
    )


def _treatment(
    source: SourceSnapshot,
    profile: Profile,
    raw: Mapping[str, object],
    turns: tuple[ScriptTurn, ...],
) -> EpisodeTreatment:
    raw_anchors = raw.get("source_anchors")
    if raw_anchors is None:
        anchors = tuple(dict.fromkeys(turn.source_anchors for turn in turns))
        flattened = tuple(anchor for group in anchors for anchor in group)
    elif isinstance(raw_anchors, list):
        flattened = tuple(
            _anchor_for_block(source, cast(str, item)) for item in raw_anchors
        )
    else:
        raise WorkflowSnapshotError("configured treatment source_anchors are invalid")
    if not flattened:
        flattened = tuple(
            SourceAnchor(block.block_id, block.start, block.end)
            for block in source.blocks
            if block.text.strip()
        )
    roles = raw.get("speaker_roles")
    if roles is None:
        speaker_roles = {
            speaker.speaker_id: "presenter" for speaker in profile.speakers
        }
    elif isinstance(roles, Mapping):
        speaker_roles = {
            _text("speaker role", key): _text("speaker role", value)
            for key, value in roles.items()
        }
    else:
        raise WorkflowSnapshotError("configured treatment speaker_roles are invalid")
    return EpisodeTreatment(
        _text("treatment_id", raw.get("treatment_id")),
        cast(
            Literal["narration", "dialogue"],
            raw.get("format_type", profile.format_type),
        ),
        _strings("narrative_arc", raw.get("narrative_arc"), ["source-bound"]),
        _positive_int(
            "target_minutes", raw.get("target_minutes"), profile.target_minutes
        ),
        _strings("sections", raw.get("sections"), ["source"]),
        speaker_roles,
        flattened,
        tuple(turn.turn_id for turn in turns)
        if turns
        else _strings("expected_turn_ids", raw.get("expected_turn_ids"), []),
    )


def _adaptation(
    source: SourceSnapshot,
    profile: Profile,
    raw: Mapping[str, object],
) -> tuple[EpisodeTreatment, tuple[ScriptTurn, ...]]:
    raw_claims = raw.get("claims")
    raw_turns = raw.get("source_turns")
    if not isinstance(raw_claims, list) or not isinstance(raw_turns, list):
        raise WorkflowSnapshotError("configured adaptation turns or claims are missing")
    claims: dict[str, Mapping[str, object]] = {}
    for item in raw_claims:
        if not isinstance(item, Mapping):
            raise WorkflowSnapshotError("configured adaptation claim is invalid")
        claim_id = _text("claim_anchor", item.get("claim_anchor"))
        if claim_id in claims:
            raise WorkflowSnapshotError("configured adaptation claims are duplicated")
        claims[claim_id] = item
    turns: list[ScriptTurn] = []
    anchors: list[SourceAnchor] = []
    for item in raw_turns:
        if not isinstance(item, Mapping):
            raise WorkflowSnapshotError("configured adaptation turn is invalid")
        turn_id = _text("turn_id", item.get("turn_id"))
        speaker_id = _text("speaker_id", item.get("speaker_id"))
        claim_id = _text("claim_anchor", item.get("claim_anchor"))
        claim = claims.get(claim_id)
        if claim is None:
            raise WorkflowSnapshotError("configured adaptation claim is missing")
        source_value = _text("source_value", claim.get("source_value"))
        adapted_value = _text(
            "adapted_value", claim.get("adapted_value", item.get("text"))
        )
        anchor = _anchor_for_text(source, source_value)
        turns.append(
            ScriptTurn(
                turn_id, speaker_id, adapted_value, "factual", (anchor,), (anchor,)
            )
        )
        anchors.append(anchor)
    if not turns:
        raise WorkflowSnapshotError("configured adaptation must contain turns")
    treatment = _treatment(
        source,
        profile,
        {
            "treatment_id": raw.get("proposal_id"),
            "format_type": profile.format_type,
            "source_anchors": [],
        },
        tuple(turns),
    )
    if not treatment.source_anchors:
        treatment = EpisodeTreatment(
            treatment.treatment_id,
            treatment.format_type,
            treatment.narrative_arc,
            treatment.target_minutes,
            treatment.sections,
            treatment.speaker_roles,
            tuple(dict.fromkeys(anchors)),
            treatment.expected_turn_ids,
        )
    return treatment, tuple(turns)


def _lexicon(
    raw: Mapping[str, object] | None,
) -> Mapping[LexiconScope, PronunciationLexicon]:
    if raw is None:
        return {}
    version = _text("lexicon version", raw.get("version", "configured-v1"))
    scope = cast(LexiconScope, raw.get("scope", "project"))
    if scope not in {"episode", "project", "domain", "global"}:
        raise WorkflowSnapshotError("configured lexicon scope is invalid")
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        raise WorkflowSnapshotError("configured lexicon entries are invalid")
    parsed = tuple(
        PronunciationEntry(
            _text("entry_id", item.get("entry_id", f"configured-{index}")),
            _text("key", item.get("key")),
            _text("spoken_form", item.get("spoken_form")),
            version,
            cast(
                Literal["name", "organization", "product", "technical_term"],
                item.get("category", "technical_term"),
            ),
        )
        for index, item in enumerate(entries, start=1)
        if isinstance(item, Mapping)
    )
    if len(parsed) != len(entries):
        raise WorkflowSnapshotError("configured lexicon entry is invalid")
    return {scope: PronunciationLexicon(scope, version, parsed)}


class ConfiguredContentWorkflowSnapshotFactory(WorkflowSnapshotFactory):
    """Bind API commands to a configured source without preparing at API time."""

    def __init__(
        self,
        content_root: str | Path,
        *,
        mode: ConfiguredRuntimeMode = "host-local",
        max_attempts: int = 2,
        render_binding: WorkflowRenderBinding | None = None,
    ) -> None:
        if mode not in {
            "deterministic-local",
            "host-local",
            "live-provider",
        }:
            raise WorkflowSnapshotError("configured runtime mode is unsupported")
        if type(max_attempts) is not int or max_attempts < 1:
            raise WorkflowSnapshotError("configured runtime attempts are invalid")
        root = Path(content_root)
        if not root.is_dir() or root.is_symlink():
            raise WorkflowSnapshotError("configured content root is unavailable")
        source_file = _read(root, "source.md")
        profile_file = _read(root, "profile.yaml")
        voices_file = _read(root, "voices.yaml")
        treatment_file = _read(root, "treatment.yaml")
        assert source_file is not None
        assert profile_file is not None
        assert voices_file is not None
        assert treatment_file is not None
        source = source_file[1]
        profile_yaml = profile_file[1]
        source_snapshot = snapshot_source(source)
        voices = _mapping(*voices_file)
        assets, consents, audio_consents, local_voices = _voices(voices)
        profile = load_profile(profile_yaml, assets, consents)
        profile_name = _text("profile_id", _mapping(*profile_file).get("profile_id"))
        if profile.profile_id != profile_name:
            raise WorkflowSnapshotError("configured profile identity is inconsistent")
        treatment_data = _mapping(*treatment_file)
        adaptation_file = _read(root, "adaptation.json", optional=True)
        adaptation = _mapping(*adaptation_file) if adaptation_file is not None else None
        if mode != "live-provider" and adaptation is None:
            raise WorkflowSnapshotError(
                "local configured runtime requires source-bound adaptation.json"
            )
        if adaptation is not None:
            treatment, turns = _adaptation(source_snapshot, profile, adaptation)
        else:
            treatment = _treatment(source_snapshot, profile, treatment_data, ())
            turns = ()
        if treatment_data.get("treatment_id") is not None:
            treatment = _treatment(source_snapshot, profile, treatment_data, turns)
        lexicon_file = _read(root, "lexicon.yaml", optional=True)
        if lexicon_file is None:
            lexicon_file = _read(root, "lexicons.yaml", optional=True)
        request = ContentPreparationRequest(
            source,
            profile_yaml,
            treatment,
            FixtureReasoningPort(
                {source_snapshot.source_sha256: AdaptationProposal(treatment, turns)}
                if turns
                else {},
                {},
            ),
            _lexicon(_mapping(*lexicon_file) if lexicon_file is not None else None),
            SegmentationCapabilities(
                _positive_int(
                    "max_text_characters",
                    treatment_data.get("max_text_characters"),
                    240,
                ),
                cast(float | None, treatment_data.get("max_duration_seconds", 10.0)),
                frozenset(speaker.speaker_id for speaker in profile.speakers),
            ),
            assets,
            consents,
        )
        if mode == "live-provider":
            if render_binding is None or render_binding.provider != "elevenlabs":
                raise WorkflowSnapshotError(
                    "live configured runtime requires a live render binding"
                )
            binding = render_binding
        else:
            if render_binding is not None:
                raise WorkflowSnapshotError(
                    "local configured runtime must not use a live binding"
                )
            provider, model = _LOCAL_BINDINGS[mode]
            binding = WorkflowRenderBinding(
                provider, model, audio_consents, max_attempts
            )
        files = [source_file, profile_file, voices_file, treatment_file]
        if adaptation_file is not None:
            files.append(adaptation_file)
        if lexicon_file is not None:
            files.append(lexicon_file)
        digest_input = b"".join(
            name.encode("utf-8") + b"\0" + text.encode("utf-8")
            for name, text in sorted((path.name, text) for path, text in files)
        )
        self._root = root.resolve()
        self._source = source
        self._source_sha256 = source_snapshot.source_sha256
        self._profile_name = profile.profile_id
        self._request = request
        self._binding = binding
        self._mode = mode
        self._local_voice_bindings = {
            speaker.speaker_id: local_voices[speaker.voice_asset_id]
            for speaker in profile.speakers
        }
        self._config_digest = hashlib.sha256(digest_input).hexdigest()

    @property
    def profile_names(self) -> frozenset[str]:
        return frozenset({self._profile_name})

    @property
    def mode(self) -> ConfiguredRuntimeMode:
        return self._mode

    @property
    def render_binding(self) -> WorkflowRenderBinding:
        return self._binding

    @property
    def local_voice_bindings(self) -> Mapping[str, str]:
        return dict(self._local_voice_bindings)

    @property
    def config_digest(self) -> str:
        return self._config_digest

    def preparation_request_for(
        self, source_markdown: str, profile_id: str
    ) -> ContentPreparationRequest:
        if source_markdown != self._source or profile_id != self._profile_name:
            raise WorkflowSnapshotError("preparation content is not configured")
        return self._request

    def build(
        self,
        *,
        record: EpisodeRecord,
        command: CommandName,
        payload: Mapping[str, object] | None,
    ) -> EpisodeWorkflowSnapshot:
        if not isinstance(record, EpisodeRecord):
            raise WorkflowSnapshotError("episode record is invalid")
        if command not in {"create", "render", "publish"}:
            raise WorkflowSnapshotError("workflow command is invalid")
        if (
            record.profile_name != self._profile_name
            or record.source_sha256 != self._source_sha256
        ):
            raise WorkflowSnapshotError("episode is not served by configured content")
        if record.source_content != self._source.encode("utf-8"):
            raise WorkflowSnapshotError(
                "episode source is not served by configured content"
            )
        requested_mode = payload.get("mode") if payload is not None else None
        if requested_mode is not None and requested_mode != self._mode:
            raise WorkflowSnapshotError(
                "command mode does not match configured runtime"
            )
        key = prepared_content_key_for(record, self._mode)
        max_cost = None
        if payload is not None and payload.get("max_cost") is not None:
            raw_cost = payload.get("max_cost")
            try:
                max_cost = Decimal(str(raw_cost))
            except (InvalidOperation, ValueError) as error:
                raise WorkflowSnapshotError(
                    "command cost ceiling is invalid"
                ) from error
            if not max_cost.is_finite() or max_cost < 0:
                raise WorkflowSnapshotError("command cost ceiling is invalid")
        preparation = {
            "tenant_id": str(record.tenant_id),
            "project_id": str(record.project_id),
            "episode_id": str(record.episode_id),
            "episode_version_id": str(record.episode_id),
            "source_markdown": self._source,
            "profile_id": self._profile_name,
            "prepared_content_key": key,
            "execution_mode": self._mode,
            "max_attempts": self._binding.max_attempts,
        }
        reference: dict[str, object] = {
            "content_config_sha256": self._config_digest,
            "prepared_content_key": key,
            "preparation_activity_input": preparation,
        }
        if payload is not None and payload.get("provider_route_id") is not None:
            reference["provider_route_id"] = _text(
                "provider_route_id", payload.get("provider_route_id")
            )
        if command == "publish" and payload is not None:
            reference["publish_payload"] = dict(payload)
        from poddown.audio.production_workflow import ProductionWorkflowInput

        production = ProductionWorkflowInput(
            episode_id=str(record.episode_id),
            episode_version_id=str(record.episode_id),
            source_sha256=record.source_sha256,
            profile_id=self._profile_name,
            prepared_content_reference=reference,
            execution_mode=self._mode,
            max_cost=max_cost,
        )
        return EpisodeWorkflowSnapshot(
            source_sha256=record.source_sha256,
            workflow_input=None,
            production_input_json=production.to_json(),
        )


__all__ = ["ConfiguredContentWorkflowSnapshotFactory"]
