"""Explicit filesystem-backed workflow snapshots for the reference runtime."""

from __future__ import annotations

import json
import platform
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml

from poddown.audio.rights import VoiceConsent as AudioVoiceConsent
from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import (
    ScriptTurn,
    SourceAnchor,
    SourceSnapshot,
    VoiceAsset,
    VoiceConsent,
)
from poddown.content.profiles import load_profile
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import (
    ContentPreparationRequest,
    ContentPreparationResult,
    prepare_content,
)
from poddown.content.source import snapshot_source
from poddown.episode_service import EpisodeRecord
from poddown.workflow_snapshots import (
    CommandName,
    EpisodeWorkflowSnapshot,
    WorkflowRenderBinding,
    WorkflowSnapshotError,
    WorkflowSnapshotFactory,
    build_workflow_snapshot,
)

ReferenceRuntimeMode = Literal["deterministic-local", "host-local", "live-provider"]
_MODE_BINDINGS: dict[ReferenceRuntimeMode, tuple[str, str]] = {
    "deterministic-local": ("local", "local-deterministic-v1"),
    "host-local": ("host-local", "host-local-tts-v1"),
}
_REFERENCE_PRONUNCIATIONS = (
    ("LiDAR", "LIE-dar"),
    ("C1", "see one"),
    ("99.7%", "ninety-nine point seven percent"),
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WorkflowSnapshotError(
            f"reference fixture is unreadable: {path.name}"
        ) from error


def _mapping(path: Path, text: str) -> Mapping[str, object]:
    try:
        value = (
            yaml.safe_load(text)
            if path.suffix in {".yaml", ".yml"}
            else json.loads(text)
        )
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise WorkflowSnapshotError(
            f"reference fixture is malformed: {path.name}"
        ) from error
    if not isinstance(value, Mapping):
        raise WorkflowSnapshotError(f"reference fixture must be an object: {path.name}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowSnapshotError(f"reference fixture field is invalid: {name}")
    return value


def _anchor_for_text(source: SourceSnapshot, value: str) -> SourceAnchor:
    encoded = value.encode("utf-8")
    try:
        start = source.source.encode("utf-8").index(encoded)
        block = next(
            block for block in source.blocks if block.start <= start < block.end
        )
    except (ValueError, StopIteration) as error:
        raise WorkflowSnapshotError(
            "reference adaptation claim is not source-bound"
        ) from error
    return SourceAnchor(block.block_id, block.start, block.end)


def _fixture_request(
    source: str,
    profile_yaml: str,
    profile_data: Mapping[str, object],
    adaptation: Mapping[str, object],
    voices: Mapping[str, object],
) -> tuple[
    ContentPreparationRequest,
    str,
    dict[str, AudioVoiceConsent],
    dict[str, str],
]:
    """Build the existing typed preparation request from reference artifacts."""
    snapshot = snapshot_source(source)
    assets_value = voices.get("assets")
    if not isinstance(assets_value, list):
        raise WorkflowSnapshotError("reference voices assets are invalid")
    assets: list[VoiceAsset] = []
    content_consents: list[VoiceConsent] = []
    audio_consents: dict[str, AudioVoiceConsent] = {}
    local_voice_names: dict[str, Mapping[str, object]] = {}
    for item in assets_value:
        if not isinstance(item, Mapping):
            raise WorkflowSnapshotError("reference voice asset is invalid")
        asset_id = _text(item.get("asset_id"), "asset_id")
        consent_id = _text(item.get("consent_id"), "consent_id")
        if item.get("synthetic") is not True or item.get("demo_only") is not True:
            raise WorkflowSnapshotError(
                "reference voice asset must be synthetic and demo-only"
            )
        if item.get("consent_status") != "approved":
            raise WorkflowSnapshotError("reference voice consent is not approved")
        local_voice = item.get("local_voice")
        if not isinstance(local_voice, Mapping):
            raise WorkflowSnapshotError("reference local voice binding is invalid")
        local_voice_names[asset_id] = local_voice
        assets.append(VoiceAsset(asset_id, True))
        content_consents.append(VoiceConsent(asset_id, True))
        audio_consents[asset_id] = AudioVoiceConsent(
            asset_id,
            consent_id,
            frozenset({"local", "host-local"}),
        )

    profile_name = _text(profile_data.get("profile_id"), "profile_id")
    profile = load_profile(profile_yaml, tuple(assets), tuple(content_consents))
    if profile.profile_id != profile_name:
        raise WorkflowSnapshotError("reference profile identity is inconsistent")

    raw_claims = adaptation.get("claims")
    raw_turns = adaptation.get("source_turns")
    if not isinstance(raw_claims, list) or not isinstance(raw_turns, list):
        raise WorkflowSnapshotError("reference adaptation turns or claims are missing")
    claims: dict[str, Mapping[str, object]] = {}
    for item in raw_claims:
        if not isinstance(item, Mapping):
            raise WorkflowSnapshotError("reference adaptation claim is invalid")
        claim_id = _text(item.get("claim_anchor"), "claim_anchor")
        claims[claim_id] = item

    turns: list[ScriptTurn] = []
    turn_anchors: list[SourceAnchor] = []
    for item in raw_turns:
        if not isinstance(item, Mapping):
            raise WorkflowSnapshotError("reference adaptation turn is invalid")
        turn_id = _text(item.get("turn_id"), "turn_id")
        speaker_id = _text(item.get("speaker_id"), "speaker_id")
        claim_id = _text(item.get("claim_anchor"), "claim_anchor")
        claim = claims.get(claim_id)
        if claim is None:
            raise WorkflowSnapshotError("reference adaptation claim is missing")
        source_value = _text(claim.get("source_value"), "source_value")
        adapted_value = _text(claim.get("adapted_value"), "adapted_value")
        anchor = _anchor_for_text(snapshot, source_value)
        turns.append(
            ScriptTurn(
                turn_id,
                speaker_id,
                adapted_value,
                "factual",
                (anchor,),
                (anchor,),
            )
        )
        turn_anchors.append(anchor)

    speaker_ids = {speaker.speaker_id for speaker in profile.speakers}
    if any(turn.speaker_id not in speaker_ids for turn in turns):
        raise WorkflowSnapshotError("reference adaptation speaker is not approved")
    expected_turn_ids = tuple(turn.turn_id for turn in turns)
    if len(set(expected_turn_ids)) != len(expected_turn_ids):
        raise WorkflowSnapshotError("reference adaptation turn IDs are not unique")
    voice_key = "macos_say_name" if platform.system() == "Darwin" else "espeak_name"
    local_voice_bindings: dict[str, str] = {}
    for speaker in profile.speakers:
        local_voice = local_voice_names.get(speaker.voice_asset_id)
        if local_voice is None:
            raise WorkflowSnapshotError("reference local voice asset is unavailable")
        voice_name = local_voice.get(voice_key)
        if not isinstance(voice_name, str) or not voice_name.strip():
            raise WorkflowSnapshotError("reference local voice name is unavailable")
        local_voice_bindings[speaker.speaker_id] = voice_name
    treatment = EpisodeTreatment(
        treatment_id=_text(adaptation.get("proposal_id"), "proposal_id"),
        format_type=profile.format_type,
        narrative_arc=("source-bound",),
        target_minutes=profile.target_minutes,
        sections=("reference",),
        speaker_roles={speaker_id: "presenter" for speaker_id in speaker_ids},
        source_anchors=tuple(dict.fromkeys(turn_anchors)),
        expected_turn_ids=expected_turn_ids,
    )
    pronunciation_entries = tuple(
        PronunciationEntry(
            f"reference-{index}",
            source_form,
            spoken_form,
            "reference-demo-lexicon-v1",
            "technical_term",
        )
        for index, (source_form, spoken_form) in enumerate(
            _REFERENCE_PRONUNCIATIONS, start=1
        )
    )
    request = ContentPreparationRequest(
        markdown=source,
        profile_yaml=profile_yaml,
        treatment=treatment,
        reasoning=FixtureReasoningPort(
            {snapshot.source_sha256: AdaptationProposal(treatment, tuple(turns))}, {}
        ),
        lexicon_layers={
            "episode": PronunciationLexicon(
                "episode", "reference-demo-lexicon-v1", pronunciation_entries
            )
        },
        capabilities=SegmentationCapabilities(
            240,
            10,
            frozenset(speaker_ids),
        ),
        voice_assets=tuple(assets),
        consents=tuple(content_consents),
    )
    return request, profile_name, audio_consents, local_voice_bindings


class ReferenceFixtureWorkflowSnapshotFactory(WorkflowSnapshotFactory):
    """Compose snapshots only for one explicitly configured reference fixture."""

    def __init__(
        self,
        fixture_root: str | Path,
        *,
        mode: ReferenceRuntimeMode = "host-local",
        max_attempts: int = 2,
        render_binding: WorkflowRenderBinding | None = None,
    ) -> None:
        if mode not in {"deterministic-local", "host-local", "live-provider"}:
            raise WorkflowSnapshotError("reference runtime mode is unsupported")
        if type(max_attempts) is not int or max_attempts < 1:
            raise WorkflowSnapshotError("reference runtime attempts are invalid")
        root = Path(fixture_root)
        if not root.is_dir() or root.is_symlink():
            raise WorkflowSnapshotError("reference fixture root is unavailable")
        source = _read_text(root / "source.md")
        profile_yaml = _read_text(root / "profile.yaml")
        adaptation = _mapping(
            root / "adaptation.json", _read_text(root / "adaptation.json")
        )
        profile_data = _mapping(root / "profile.yaml", profile_yaml)
        voices = _mapping(root / "voices.yaml", _read_text(root / "voices.yaml"))
        request, profile_name, audio_consents, local_voice_bindings = _fixture_request(
            source, profile_yaml, profile_data, adaptation, voices
        )
        try:
            prepared = prepare_content(request)
        except (TypeError, ValueError) as error:
            raise WorkflowSnapshotError(
                "reference content preparation failed"
            ) from error
        if not isinstance(prepared, ContentPreparationResult):
            raise WorkflowSnapshotError(
                "reference content preparation returned an invalid result"
            )
        if mode == "live-provider":
            if render_binding is None or render_binding.provider != "elevenlabs":
                raise WorkflowSnapshotError(
                    "live reference runtime requires an ElevenLabs binding"
                )
            binding = render_binding
        else:
            if render_binding is not None:
                raise WorkflowSnapshotError(
                    "local reference runtime must not use a live binding"
                )
            provider, model = _MODE_BINDINGS[mode]
            binding = WorkflowRenderBinding(
                provider,
                model,
                audio_consents,
                max_attempts,
            )
        self._source_content = source.encode("utf-8")
        self._source_sha256 = prepared.snapshot.source_sha256
        self._profile_name = profile_name
        self._preparation_request = request
        self._prepared = prepared
        self._mode = mode
        self._local_voice_bindings = local_voice_bindings
        self._binding = binding

    @property
    def profile_names(self) -> frozenset[str]:
        """Return the exact profile name served by this fixture."""
        return frozenset({self._profile_name})

    @property
    def mode(self) -> ReferenceRuntimeMode:
        """Return the explicit local execution mode of this factory."""
        return self._mode

    @property
    def render_binding(self) -> WorkflowRenderBinding:
        """Return the immutable provider/consent binding for worker projection."""
        return self._binding

    @property
    def local_voice_bindings(self) -> Mapping[str, str]:
        """Return the platform-specific host-local voice names."""
        return dict(self._local_voice_bindings)

    @property
    def prepared_content(self) -> ContentPreparationResult:
        """Return the validated fixture content for worker-owned stage adapters."""
        return self._prepared

    def preparation_request_for(
        self, source_markdown: str, profile_id: str
    ) -> ContentPreparationRequest:
        """Return the validated fixture request for the preparation activity."""
        if source_markdown != self._source_content.decode("utf-8"):
            raise WorkflowSnapshotError(
                "preparation source is not served by this fixture"
            )
        if profile_id != self._profile_name:
            raise WorkflowSnapshotError(
                "preparation profile is not served by this fixture"
            )
        return self._preparation_request

    def build(
        self,
        *,
        record: EpisodeRecord,
        command: CommandName,
        payload: Mapping[str, object] | None,
    ) -> EpisodeWorkflowSnapshot:
        """Bind the prepared fixture to one durable episode record."""
        if not isinstance(record, EpisodeRecord):
            raise WorkflowSnapshotError("episode record is invalid")
        if command not in {"create", "render", "publish"}:
            raise WorkflowSnapshotError("workflow command is invalid")
        if record.profile_name != self._profile_name:
            raise WorkflowSnapshotError("episode profile is not served by this fixture")
        if (
            record.source_sha256 != self._source_sha256
            or record.source_content != self._source_content
        ):
            raise WorkflowSnapshotError("episode source is not served by this fixture")
        if payload is not None:
            requested_mode = payload.get("mode")
            if requested_mode is not None and requested_mode != self._mode:
                raise WorkflowSnapshotError("command mode does not match runtime mode")
        return build_workflow_snapshot(self._prepared, record, self._binding)


__all__ = ["ReferenceFixtureWorkflowSnapshotFactory", "ReferenceRuntimeMode"]
