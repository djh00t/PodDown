"""Source-bound immutable snapshots for API-to-Temporal command dispatch."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol, cast

from poddown.audio.contracts import RenderRequest
from poddown.audio.production_workflow import ProductionWorkflowInput
from poddown.audio.rights import VoiceConsent
from poddown.audio.workflow import EpisodeWorkflowInput, SegmentWorkflowInput
from poddown.content.models import ScriptTurn
from poddown.content.service import ContentPreparationResult
from poddown.content.tokens import CriticalToken
from poddown.episode_service import EpisodeRecord

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
CommandName = Literal["create", "render", "publish"]


class WorkflowSnapshotError(ValueError):
    """Raised when a command cannot be bound to an immutable source snapshot."""


def prepared_content_key_for(
    record: EpisodeRecord,
    execution_mode: Literal["deterministic-local", "host-local", "live-provider"],
) -> str:
    """Return the stable worker-store key for one episode preparation."""
    if not isinstance(record, EpisodeRecord):
        raise WorkflowSnapshotError("episode record is invalid")
    if execution_mode not in {
        "deterministic-local",
        "host-local",
        "live-provider",
    }:
        raise WorkflowSnapshotError("execution mode is invalid")
    identity = {
        "tenant_id": str(record.tenant_id),
        "project_id": str(record.project_id),
        "episode_id": str(record.episode_id),
        "episode_version_id": str(record.episode_id),
        "source_sha256": record.source_sha256,
        "profile_id": record.profile_name,
        "execution_mode": execution_mode,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class EpisodeWorkflowSnapshot:
    """The source identity and render snapshot carried by one command."""

    source_sha256: str
    workflow_input: EpisodeWorkflowInput | None
    production_input_json: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_sha256, str)
            or _SHA256.fullmatch(self.source_sha256) is None
        ):
            raise WorkflowSnapshotError("source snapshot hash is invalid")
        if self.workflow_input is not None:
            if not isinstance(self.workflow_input, EpisodeWorkflowInput):
                raise WorkflowSnapshotError("workflow input snapshot is invalid")
            if any(
                segment.render_request.episode_id != self.workflow_input.episode_id
                or segment.render_request.episode_version
                != self.workflow_input.episode_version
                for segment in self.workflow_input.segments
            ):
                raise WorkflowSnapshotError("workflow input identity is inconsistent")
        elif self.production_input_json is None:
            raise WorkflowSnapshotError(
                "source-only snapshots require production workflow authority"
            )
        if self.production_input_json is not None:
            try:
                production = ProductionWorkflowInput.from_json(
                    self.production_input_json
                )
            except (TypeError, ValueError) as error:
                raise WorkflowSnapshotError(
                    "production workflow input snapshot is invalid"
                ) from error
            if production.source_sha256 != self.source_sha256:
                raise WorkflowSnapshotError(
                    "production workflow input identity is inconsistent"
                )
            if self.workflow_input is None:
                return
            if (
                production.episode_id != self.workflow_input.episode_id
                or production.prepared_content_reference.get(
                    "episode_render_workflow_input_json"
                )
                != self.workflow_input.to_json()
            ):
                raise WorkflowSnapshotError(
                    "production workflow input identity is inconsistent"
                )

    def to_payload(self) -> dict[str, str]:
        """Return the exact JSON-shaped command fields persisted for replay."""
        payload = {"source_sha256": self.source_sha256}
        if self.workflow_input is not None:
            payload["workflow_input"] = self.workflow_input.to_json()
        if self.production_input_json is not None:
            payload["production_workflow_input"] = self.production_input_json
        return payload


class WorkflowSnapshotFactory(Protocol):
    """Port for composing a source-bound snapshot from durable episode state."""

    def build(
        self,
        *,
        record: EpisodeRecord,
        command: CommandName,
        payload: Mapping[str, object] | None,
    ) -> EpisodeWorkflowSnapshot:
        """Build one immutable snapshot without mutating the episode record."""


@dataclass(frozen=True, slots=True)
class WorkflowRenderBinding:
    """Provider and consent evidence required to turn prepared turns into work."""

    provider: str
    model: str
    consents: Mapping[str, VoiceConsent]
    max_attempts: int = 2
    voice_asset_ids: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise WorkflowSnapshotError("render provider is invalid")
        if not isinstance(self.model, str) or not self.model.strip():
            raise WorkflowSnapshotError("render model is invalid")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise WorkflowSnapshotError("render attempt count is invalid")
        consents = dict(self.consents)
        if not consents or any(
            not isinstance(asset_id, str)
            or not asset_id
            or not isinstance(consent, VoiceConsent)
            for asset_id, consent in consents.items()
        ):
            raise WorkflowSnapshotError("render consent binding is invalid")
        object.__setattr__(self, "consents", MappingProxyType(consents))
        voice_asset_ids = dict(self.voice_asset_ids)
        if any(
            not isinstance(source_id, str)
            or not source_id
            or not isinstance(provider_id, str)
            or not provider_id
            for source_id, provider_id in voice_asset_ids.items()
        ):
            raise WorkflowSnapshotError("voice asset mapping is invalid")
        object.__setattr__(self, "voice_asset_ids", MappingProxyType(voice_asset_ids))

    def provider_voice_asset_id(self, source_voice_asset_id: str) -> str:
        """Resolve one source profile voice to its pinned provider identity."""
        try:
            return self.voice_asset_ids.get(
                source_voice_asset_id, source_voice_asset_id
            )
        except AttributeError as error:
            raise WorkflowSnapshotError("voice asset mapping is invalid") from error


def _spoken_text(text: str, tokens: tuple[CriticalToken, ...]) -> str:
    """Apply source-bound pronunciation forms without changing unsupported text."""
    spoken = text
    replacements: dict[str, str] = {}
    for index, token in enumerate(
        sorted(tokens, key=lambda item: len(item.source_form), reverse=True)
    ):
        marker = f"\x00poddown-token-{index}\x00"
        spoken = spoken.replace(token.source_form, marker)
        replacements[marker] = token.expected_spoken_form
    for marker, spoken_form in replacements.items():
        spoken = spoken.replace(marker, spoken_form)
    return " ".join(spoken.split())


def _turn_tokens(
    turn: ScriptTurn, result: ContentPreparationResult
) -> tuple[CriticalToken, ...]:
    """Select critical-token occurrences whose source span belongs to one turn."""
    if not turn.source_anchors:
        return ()
    return tuple(
        token
        for token in result.tokens
        if token.script_span is not None
        and any(
            anchor.start <= token.source_span[0] and token.source_span[1] <= anchor.end
            for anchor in turn.source_anchors
        )
    )


def build_render_workflow_input(
    result: ContentPreparationResult,
    *,
    episode_id: str,
    episode_version: str,
    binding: WorkflowRenderBinding,
    tenant_id: str | None = None,
    project_id: str | None = None,
) -> EpisodeWorkflowInput:
    """Build a render snapshot from canonical preparation evidence."""
    if not isinstance(result, ContentPreparationResult):
        raise TypeError("result must be ContentPreparationResult")
    if not isinstance(episode_id, str) or not episode_id:
        raise WorkflowSnapshotError("render episode ID is invalid")
    if not isinstance(episode_version, str) or not episode_version:
        raise WorkflowSnapshotError("render episode version is invalid")
    speakers = {speaker.speaker_id: speaker for speaker in result.profile.speakers}
    segments: list[SegmentWorkflowInput] = []
    for turn in result.script.turns:
        speaker = speakers.get(turn.speaker_id)
        if speaker is None:
            raise WorkflowSnapshotError("prepared turn speaker is not in profile")
        provider_voice_asset_id = binding.provider_voice_asset_id(
            speaker.voice_asset_id
        )
        try:
            consent = binding.consents[provider_voice_asset_id]
        except KeyError as error:
            raise WorkflowSnapshotError(
                "prepared voice consent is unavailable"
            ) from error
        tokens = _turn_tokens(turn, result)
        segment_id = f"turn-{turn.turn_id}"
        segments.append(
            SegmentWorkflowInput(
                segment_id=segment_id,
                render_request=RenderRequest(
                    episode_id=episode_id,
                    episode_version=episode_version,
                    segment_id=segment_id,
                    speaker_id=turn.speaker_id,
                    expected_spoken_text=_spoken_text(turn.text, tokens),
                    voice_asset_id=provider_voice_asset_id,
                    provider=binding.provider,
                    model=binding.model,
                ),
                consent=consent,
                critical_tokens=tuple(token.expected_spoken_form for token in tokens),
            )
        )
    try:
        return EpisodeWorkflowInput(
            episode_id=episode_id,
            episode_version=episode_version,
            segments=tuple(segments),
            max_attempts=binding.max_attempts,
            tenant_id=tenant_id,
            project_id=project_id,
        )
    except (TypeError, ValueError) as error:
        raise WorkflowSnapshotError(
            "prepared content cannot form a render workflow input"
        ) from error


def build_workflow_snapshot(
    result: ContentPreparationResult,
    record: EpisodeRecord,
    binding: WorkflowRenderBinding,
) -> EpisodeWorkflowSnapshot:
    """Build an immutable per-turn render snapshot from validated content."""
    if result.snapshot.source_sha256 != record.source_sha256:
        raise WorkflowSnapshotError("prepared content source does not match episode")
    render_input = build_render_workflow_input(
        result,
        episode_id=str(record.episode_id),
        episode_version=f"v{record.version}",
        binding=binding,
        tenant_id=(str(record.tenant_id) if binding.provider == "elevenlabs" else None),
        project_id=(
            str(record.project_id) if binding.provider == "elevenlabs" else None
        ),
    )
    try:
        raw_execution_mode = {
            "local": "deterministic-local",
            "host-local": "host-local",
            "elevenlabs": "live-provider",
        }.get(binding.provider)
        production_input_json = None
        if raw_execution_mode is not None:
            execution_mode = cast(
                Literal["deterministic-local", "host-local", "live-provider"],
                raw_execution_mode,
            )
            prepared_content_key = prepared_content_key_for(record, execution_mode)
            production_input_json = ProductionWorkflowInput(
                episode_id=str(record.episode_id),
                episode_version_id=str(record.episode_id),
                source_sha256=record.source_sha256,
                profile_id=result.profile.profile_id,
                prepared_content_reference={
                    "manifest_sha256": result.manifest_sha256,
                    "prepared_content_key": prepared_content_key,
                    "episode_render_workflow_input_json": render_input.to_json(),
                    "preparation_activity_input": {
                        "tenant_id": str(record.tenant_id),
                        "project_id": str(record.project_id),
                        "episode_id": str(record.episode_id),
                        "episode_version_id": str(record.episode_id),
                        "source_markdown": record.source_content.decode("utf-8"),
                        "profile_id": result.profile.profile_id,
                        "prepared_content_key": prepared_content_key,
                        "execution_mode": execution_mode,
                        "max_attempts": binding.max_attempts,
                    },
                },
                execution_mode=execution_mode,
            ).to_json()
        return EpisodeWorkflowSnapshot(
            source_sha256=record.source_sha256,
            workflow_input=render_input,
            production_input_json=production_input_json,
        )
    except (TypeError, ValueError) as error:
        raise WorkflowSnapshotError(
            "prepared content cannot form a workflow snapshot"
        ) from error


PreparationFactory = Callable[
    [EpisodeRecord, CommandName, Mapping[str, object] | None], ContentPreparationResult
]


@dataclass(frozen=True, slots=True)
class PreparedContentWorkflowSnapshotFactory:
    """Compose API snapshots through an injected validated content preparer."""

    prepare: PreparationFactory
    binding: WorkflowRenderBinding

    def build(
        self,
        *,
        record: EpisodeRecord,
        command: CommandName,
        payload: Mapping[str, object] | None,
    ) -> EpisodeWorkflowSnapshot:
        """Prepare source-bound content and freeze its render inputs."""
        try:
            result = self.prepare(record, command, payload)
        except (TypeError, ValueError, LookupError) as error:
            raise WorkflowSnapshotError("content preparation failed") from error
        if not isinstance(result, ContentPreparationResult):
            raise WorkflowSnapshotError(
                "content preparation returned an invalid result"
            )
        return build_workflow_snapshot(result, record, self.binding)


def bind_snapshot_to_record(
    snapshot: EpisodeWorkflowSnapshot, record: EpisodeRecord
) -> EpisodeWorkflowSnapshot:
    """Reject a factory result that is not bound to the requested episode."""
    if snapshot.source_sha256 != record.source_sha256:
        raise WorkflowSnapshotError("workflow snapshot source does not match episode")
    if snapshot.workflow_input is not None:
        if snapshot.workflow_input.episode_id != str(record.episode_id):
            raise WorkflowSnapshotError(
                "workflow snapshot episode does not match record"
            )
        if snapshot.workflow_input.episode_version != f"v{record.version}":
            raise WorkflowSnapshotError(
                "workflow snapshot version does not match record"
            )
    else:
        if snapshot.production_input_json is None:
            raise WorkflowSnapshotError("production workflow authority is missing")
        try:
            production = ProductionWorkflowInput.from_json(
                snapshot.production_input_json
            )
        except (TypeError, ValueError) as error:
            raise WorkflowSnapshotError(
                "production workflow authority is invalid"
            ) from error
        if production.episode_id != str(record.episode_id):
            raise WorkflowSnapshotError(
                "production workflow episode does not match record"
            )
        if production.episode_version_id != str(record.episode_id):
            raise WorkflowSnapshotError(
                "production workflow version does not match record"
            )
    return snapshot


__all__ = [
    "EpisodeWorkflowSnapshot",
    "PreparedContentWorkflowSnapshotFactory",
    "WorkflowRenderBinding",
    "WorkflowSnapshotError",
    "WorkflowSnapshotFactory",
    "build_render_workflow_input",
    "build_workflow_snapshot",
    "bind_snapshot_to_record",
    "prepared_content_key_for",
]
