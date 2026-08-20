"""Durable activity adapters for mastering, final-master QA, and packaging."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter_ns
from typing import Any, cast
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from poddown.artifacts import ArtifactRef as PackageArtifactRef
from poddown.artifacts import ArtifactStore
from poddown.artifacts import FilesystemArtifactStore as PackageArtifactStore
from poddown.audio.mastering import (
    MasteredAudio,
    MasteringProfile,
    MasteringRequest,
    MasteringSegment,
    MasteringService,
)
from poddown.audio.prepare_activity import (
    LiveAdaptationFactory,
    PreparationRequestFactory,
    PreparedContentRecorder,
    RenderSnapshotFactory,
    build_json_prepare_content_activity,
)
from poddown.audio.production_workflow import (
    FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME,
    MASTER_EPISODE_ACTIVITY_NAME,
    PACKAGE_EPISODE_ACTIVITY_NAME,
    MasterAndFinalMasterQaInput,
    ProductionWorkflowInput,
    build_validate_source_activity,
    render_workflow_input_for,
    render_workflow_input_from_preparation,
)
from poddown.audio.storage import (
    ArtifactIntegrityError,
    FilesystemRenderRecordStore,
)
from poddown.audio.storage import (
    FilesystemArtifactStore as RenderArtifactStore,
)
from poddown.content.service import ContentPreparationResult
from poddown.domain import FidelityResult, ProviderUsage
from poddown.package_generation import (
    PackageGenerationInput,
    build_package_provenance,
    generate_package_artifacts,
)
from poddown.packages import (
    EpisodePackageService,
    manifest_sha256_for,
    package_sha256_for,
)
from poddown.providers.contracts import (
    ProviderEvidence,
    Transcriber,
    TranscriptResult,
    TranscriptWord,
    provider_usage_to_mapping,
)
from poddown.qa.final_master import (
    FinalMasterGate,
    FinalMasterQaResult,
    FinalMasterQaService,
)

_STAGE_KEY = re.compile(r"^episode-production-[0-9a-f]{64}$")
_PACKAGE_ARTIFACT_MEDIA = {
    "episode.wav": "audio/wav",
    "episode.mp3": "audio/mpeg",
}


class ProductionActivityError(RuntimeError):
    """Raised when a durable production activity cannot prove its evidence."""


PreparedContentResolver = Callable[[ProductionWorkflowInput], ContentPreparationResult]
TranscriberFactory = Callable[
    [ProductionWorkflowInput, ContentPreparationResult], Transcriber
]
ProviderEvidenceRecorder = Callable[[ProviderEvidence], object]
ProviderEvidenceRecorderFactory = Callable[
    [ProductionWorkflowInput], ProviderEvidenceRecorder
]
PackageCompletionRecorder = Callable[
    [ProductionWorkflowInput, FinalMasterQaResult, str, str], object
]


def _stage_key(value: ProductionWorkflowInput) -> str:
    key = f"episode-production-{value.digest()}"
    if _STAGE_KEY.fullmatch(key) is None:
        raise ProductionActivityError("production stage identity is invalid")
    return key


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    with NamedTemporaryFile(
        dir=path.parent, mode="w", encoding="utf-8", delete=False
    ) as temporary:
        temporary.write(payload)
        temporary.flush()
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _artifact_dict(reference: PackageArtifactRef) -> dict[str, object]:
    return {
        "name": reference.name,
        "media_type": reference.media_type,
        "byte_count": reference.byte_count,
        "sha256": reference.sha256,
        "storage_key": reference.storage_key,
    }


def _artifact_from_dict(value: object) -> PackageArtifactRef:
    if not isinstance(value, Mapping):
        raise ProductionActivityError("stage artifact reference is malformed")
    try:
        return PackageArtifactRef(
            name=cast(str, value["name"]),
            media_type=cast(str, value["media_type"]),
            byte_count=cast(int, value["byte_count"]),
            sha256=cast(str, value["sha256"]),
            storage_key=cast(str, value["storage_key"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionActivityError(
            "stage artifact reference is malformed"
        ) from error


def _diagnostics_from_dict(value: object) -> Any:
    from poddown.audio.diagnostics import AudioDiagnostics

    if not isinstance(value, Mapping):
        raise ProductionActivityError("stage diagnostics are malformed")
    try:
        return AudioDiagnostics(
            sample_rate_hz=cast(int, value["sample_rate_hz"]),
            channels=cast(int, value["channels"]),
            duration_seconds=cast(float, value["duration_seconds"]),
            peak_amplitude=cast(float, value["peak_amplitude"]),
            clipping_ratio=cast(float, value["clipping_ratio"]),
            silence_ratio=cast(float, value["silence_ratio"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionActivityError("stage diagnostics are malformed") from error


class ProductionStageStore:
    """Persist master bytes and final QA evidence for cross-activity replay."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._artifacts = PackageArtifactStore(self._root / "artifacts")
        self._records = self._root / "records"
        self._records.mkdir(parents=True, exist_ok=True)

    def save_master(self, key: str, master: MasteredAudio) -> None:
        """Persist master bytes by content address and immutable provenance."""
        self._require_key(key)
        wav = self._artifacts.put(
            f"master-{master.provenance.wav_checksum}.wav",
            "audio/wav",
            master.wav_bytes,
        )
        mp3 = self._artifacts.put(
            f"master-{master.provenance.mp3_checksum}.mp3",
            "audio/mpeg",
            master.mp3_bytes,
        )
        self._write(
            key,
            "master",
            {
                "episode_id": master.episode_id,
                "episode_version": master.episode_version,
                "wav": _artifact_dict(wav),
                "mp3": _artifact_dict(mp3),
                "diagnostics": master.diagnostics.to_dict(),
                "provenance": {
                    "profile_version": master.provenance.profile_version,
                    "ffmpeg_executable": master.provenance.ffmpeg_executable,
                    "ffmpeg_version": master.provenance.ffmpeg_version,
                    "command": list(master.provenance.command),
                    "filters": list(master.provenance.filters),
                    "input_checksums": list(master.provenance.input_checksums),
                    "wav_checksum": master.provenance.wav_checksum,
                    "mp3_checksum": master.provenance.mp3_checksum,
                    "wav_metadata": dict(master.provenance.wav_metadata),
                    "mp3_metadata": dict(master.provenance.mp3_metadata),
                    "commands": [
                        list(command) for command in master.provenance.commands
                    ],
                },
            },
        )

    def load_master(self, key: str) -> MasteredAudio:
        """Load and verify a previously committed master."""
        payload = self._read(key, "master")
        try:
            provenance = payload["provenance"]
            if not isinstance(provenance, Mapping):
                raise ValueError("master provenance is malformed")
            from poddown.audio.mastering import MasteringProvenance

            parsed_provenance = MasteringProvenance(
                profile_version=cast(str, provenance["profile_version"]),
                ffmpeg_executable=cast(str, provenance["ffmpeg_executable"]),
                ffmpeg_version=cast(str, provenance["ffmpeg_version"]),
                command=tuple(cast(list[str], provenance["command"])),
                filters=tuple(cast(list[str], provenance["filters"])),
                input_checksums=tuple(cast(list[str], provenance["input_checksums"])),
                wav_checksum=cast(str, provenance["wav_checksum"]),
                mp3_checksum=cast(str, provenance["mp3_checksum"]),
                wav_metadata=cast(Mapping[str, object], provenance["wav_metadata"]),
                mp3_metadata=cast(Mapping[str, object], provenance["mp3_metadata"]),
                commands=tuple(
                    tuple(command)
                    for command in cast(list[list[str]], provenance["commands"])
                ),
            )
            wav_reference = _artifact_from_dict(payload["wav"])
            mp3_reference = _artifact_from_dict(payload["mp3"])
            wav = self._artifacts.read(wav_reference)
            mp3 = self._artifacts.read(mp3_reference)
            return MasteredAudio(
                episode_id=cast(str, payload["episode_id"]),
                episode_version=cast(str, payload["episode_version"]),
                wav_bytes=wav,
                mp3_bytes=mp3,
                diagnostics=_diagnostics_from_dict(payload["diagnostics"]),
                provenance=parsed_provenance,
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            raise ProductionActivityError(
                "persisted master evidence is malformed"
            ) from error

    def save_qa(self, key: str, result: FinalMasterQaResult) -> None:
        """Persist the complete final-master transcript and gate evidence."""
        self._require_key(key)
        transcript = result.transcript
        self._write(
            key,
            "qa",
            {
                "subject_id": result.subject_id,
                "master_checksum": result.master_checksum,
                "transcript": {
                    "text": transcript.text,
                    "words": [asdict(word) for word in transcript.words],
                    "provider": transcript.provider,
                    "model": transcript.model,
                    "usage": asdict(transcript.usage),
                    "request_id": transcript.request_id,
                    "checksum": transcript.checksum,
                    "cost": str(transcript.cost),
                    "confidence": transcript.confidence,
                    "mode": transcript.mode,
                },
                "fidelity": asdict(result.fidelity),
                "gates": [gate.to_dict() for gate in result.gates],
                "diagnostics": result.diagnostics.to_dict(),
            },
        )

    def load_qa(self, key: str) -> FinalMasterQaResult:
        """Load one complete QA result and verify its transcript binding."""
        payload = self._read(key, "qa")
        try:
            transcript = payload["transcript"]
            if not isinstance(transcript, Mapping):
                raise ValueError("transcript is malformed")
            usage = transcript["usage"]
            if not isinstance(usage, Mapping):
                raise ValueError("transcript usage is malformed")
            words = tuple(
                TranscriptWord(
                    word=cast(str, item["word"]),
                    start=cast(float, item["start"]),
                    end=cast(float, item["end"]),
                )
                for item in cast(list[Mapping[str, object]], transcript["words"])
            )
            result = FinalMasterQaResult(
                subject_id=cast(str, payload["subject_id"]),
                master_checksum=cast(str, payload["master_checksum"]),
                transcript=TranscriptResult(
                    text=cast(str, transcript["text"]),
                    words=words,
                    provider=cast(str, transcript["provider"]),
                    model=cast(str, transcript["model"]),
                    usage=ProviderUsage(
                        input_units=cast(int, usage["input_units"]),
                        output_units=cast(int, usage["output_units"]),
                    ),
                    request_id=cast(str, transcript["request_id"]),
                    checksum=cast(str, transcript["checksum"]),
                    cost=Decimal(cast(str, transcript["cost"])),
                    confidence=cast(float | None, transcript.get("confidence")),
                    mode=cast(str, transcript["mode"]),
                ),
                fidelity=FidelityResult(**cast(dict[str, Any], payload["fidelity"])),
                gates=tuple(
                    FinalMasterGate(
                        name=cast(str, gate["name"]),
                        passed=cast(bool, gate["passed"]),
                        evidence=cast(Mapping[str, object], gate["evidence"]),
                    )
                    for gate in cast(list[Mapping[str, object]], payload["gates"])
                ),
                diagnostics=_diagnostics_from_dict(payload["diagnostics"]),
            )
            if not result.passed:
                raise ValueError("persisted final-master QA did not pass")
            return result
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            raise ProductionActivityError(
                "persisted final-master QA is malformed"
            ) from error

    def _write(self, key: str, suffix: str, payload: Mapping[str, object]) -> None:
        self._require_key(key)
        path = self._records / f"{key}.{suffix}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ProductionActivityError("production stage evidence conflicts")
            return
        _atomic_json(path, payload)

    def _read(self, key: str, suffix: str) -> dict[str, object]:
        self._require_key(key)
        path = self._records / f"{key}.{suffix}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProductionActivityError(
                "production stage evidence is unavailable"
            ) from error
        if not isinstance(value, dict):
            raise ProductionActivityError("production stage evidence is malformed")
        return value

    @staticmethod
    def _require_key(key: str) -> None:
        if _STAGE_KEY.fullmatch(key) is None:
            raise ProductionActivityError("production stage identity is invalid")


@dataclass(frozen=True)
class ProductionActivityDependencies:
    """Worker-owned ports used by the durable production stage activities."""

    render_artifacts: RenderArtifactStore
    render_records: FilesystemRenderRecordStore
    stages: ProductionStageStore
    packages: EpisodePackageService
    package_artifacts: ArtifactStore
    prepared_content: PreparedContentResolver
    mastering: MasteringService
    transcriber_factory: TranscriberFactory
    renderer_identity: str
    provider_evidence_recorder_factory: ProviderEvidenceRecorderFactory | None = None
    package_completion_recorder: PackageCompletionRecorder | None = None


class ScriptDerivedTranscriber:
    """Explicit offline transcriber for deterministic and host-local workflows."""

    def __init__(self, text: str, *, provider: str, model: str, mode: str) -> None:
        self._text = text
        self._provider = provider
        self._model = model
        self._mode = mode

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        words = tuple(
            TranscriptWord(word, index * 0.1, (index + 1) * 0.1)
            for index, word in enumerate(self._text.split())
        )
        return TranscriptResult(
            text=self._text,
            words=words,
            provider=self._provider,
            model=self._model,
            usage=ProviderUsage(len(audio), len(words)),
            request_id=f"{self._mode}-script-derived-final-master",
            checksum=sha256(audio).hexdigest(),
            mode=self._mode,
        )


def _production_input(value: object) -> ProductionWorkflowInput:
    if not isinstance(value, Mapping):
        raise ProductionActivityError("production input is malformed")
    try:
        return ProductionWorkflowInput.from_json(
            json.dumps(value, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError) as error:
        raise ProductionActivityError("production input is malformed") from error


def _handoff(value: object) -> MasterAndFinalMasterQaInput:
    if not isinstance(value, Mapping):
        raise ProductionActivityError("master handoff is malformed")
    try:
        selected = value["selected_candidate_ids"]
        preparation = value["preparation_result"]
        if not isinstance(selected, list) or not isinstance(preparation, Mapping):
            raise ValueError("master handoff is malformed")
        return MasterAndFinalMasterQaInput(
            episode_id=cast(str, value["episode_id"]),
            episode_version_id=cast(str, value["episode_version_id"]),
            render_input_sha256=cast(str, value["render_input_sha256"]),
            render_result_sha256=cast(str, value["render_result_sha256"]),
            selected_candidate_ids=tuple(cast(str, item) for item in selected),
            production_input_json=cast(str, value["production_input_json"]),
            preparation_result=cast(Mapping[str, object], preparation),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionActivityError("master handoff is malformed") from error


def _spoken_text(prepared: ContentPreparationResult) -> str:
    text = " ".join(turn.text for turn in prepared.script.turns)
    replacements: dict[str, str] = {}
    for index, token in enumerate(
        sorted(prepared.tokens, key=lambda item: len(item.source_form), reverse=True)
    ):
        marker = f"\x00poddown-token-{index}\x00"
        text = text.replace(token.source_form, marker)
        replacements[marker] = token.expected_spoken_form
    for marker, spoken in replacements.items():
        text = text.replace(marker, spoken)
    return " ".join(text.split())


def spoken_text_for_prepared(prepared: ContentPreparationResult) -> str:
    """Return source-bound expected speech with approved token forms applied."""
    if not isinstance(prepared, ContentPreparationResult):
        raise TypeError("prepared content must be ContentPreparationResult")
    return _spoken_text(prepared)


def _profile_for(prepared: ContentPreparationResult, mode: str) -> MasteringProfile:
    return MasteringProfile(
        silence_ms=0,
        min_duration_seconds=0.01,
        max_duration_seconds=max(60.0, prepared.profile.target_minutes * 120.0),
        max_peak_amplitude=0.99 if mode == "host-local" else 1.0,
        version=prepared.profile.version,
    )


def _selected_segments(
    handoff: MasterAndFinalMasterQaInput,
    dependencies: ProductionActivityDependencies,
) -> tuple[MasteringSegment, ...]:
    from dataclasses import replace

    try:
        production = ProductionWorkflowInput.from_json(handoff.production_input_json)
        if "render_workflow_input_json" in handoff.preparation_result:
            render_input = render_workflow_input_from_preparation(
                production, handoff.preparation_result
            )
        else:
            render_input = render_workflow_input_for(production)
    except (TypeError, ValueError) as error:
        raise ProductionActivityError("render evidence is malformed") from error
    selected: list[MasteringSegment] = []
    for position, (segment, accepted) in enumerate(
        zip(render_input.segments, handoff.selected_candidate_ids, strict=True)
    ):
        outcome = None
        for attempt in range(1, render_input.max_attempts + 1):
            for take in range(3):
                request = replace(
                    segment.render_request, attempt=attempt, take_index=take
                )
                if request.candidate_id != accepted:
                    continue
                outcome = dependencies.render_records.find(request.idempotency_key)
                break
            if outcome is not None:
                break
        if outcome is None or outcome.candidate.candidate_id != accepted:
            raise ProductionActivityError("selected render artifact is unavailable")
        try:
            audio = dependencies.render_artifacts.read(outcome.candidate.artifact)
        except (ArtifactIntegrityError, OSError, ValueError) as error:
            raise ProductionActivityError(
                "selected render artifact is invalid"
            ) from error
        selected.append(MasteringSegment(position, segment.segment_id, audio))
    return tuple(selected)


def build_master_activity(
    dependencies: ProductionActivityDependencies,
) -> Callable[..., object]:
    """Build the durable mastering activity over selected render records."""

    @activity.defn(name=MASTER_EPISODE_ACTIVITY_NAME)
    def master(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            handoff = _handoff(payload)
            production = ProductionWorkflowInput.from_json(
                handoff.production_input_json
            )
            if (
                handoff.episode_id != production.episode_id
                or handoff.episode_version_id != production.episode_version_id
            ):
                raise ProductionActivityError("master handoff identity is inconsistent")
            prepared = dependencies.prepared_content(production)
            if prepared.snapshot.source_sha256 != production.source_sha256:
                raise ProductionActivityError("prepared content source is inconsistent")
            profile = _profile_for(prepared, production.execution_mode)
            master = dependencies.mastering.master(
                MasteringRequest(
                    production.episode_id,
                    production.episode_version_id,
                    _selected_segments(handoff, dependencies),
                    profile,
                )
            )
            key = _stage_key(production)
            dependencies.stages.save_master(key, master)
            return {
                "episode_id": production.episode_id,
                "episode_version_id": production.episode_version_id,
                "passed": True,
                "master_wav_checksum": master.provenance.wav_checksum,
                "master_mp3_checksum": master.provenance.mp3_checksum,
                "stage_record_key": key,
            }
        except ProductionActivityError as error:
            raise ApplicationError(
                "master activity rejected",
                type="ProductionStageOutputRejected",
                non_retryable=True,
            ) from error
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ApplicationError(
                "master activity rejected",
                type="ProductionStageOutputRejected",
                non_retryable=True,
            ) from error

    return master


def build_final_master_transcription_activity(
    dependencies: ProductionActivityDependencies,
) -> Callable[..., object]:
    """Build the checksum-bound final-master transcription and QA activity."""

    @activity.defn(name=FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME)
    async def final_master(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            production = _production_input(payload.get("production_input"))
            master_payload = payload.get("master")
            if not isinstance(master_payload, Mapping):
                raise ProductionActivityError("master output is malformed")
            key = master_payload.get("stage_record_key")
            if not isinstance(key, str) or key != _stage_key(production):
                raise ProductionActivityError("master stage identity is invalid")
            master = dependencies.stages.load_master(key)
            prepared = dependencies.prepared_content(production)
            profile = _profile_for(prepared, production.execution_mode)
            started_ns = perf_counter_ns()
            transcriber = dependencies.transcriber_factory(production, prepared)
            qa = await FinalMasterQaService(transcriber).evaluate(
                master,
                profile,
                tuple(token.expected_spoken_form for token in prepared.tokens),
            )
            evidence_factory = dependencies.provider_evidence_recorder_factory
            if production.execution_mode == "live-provider":
                if evidence_factory is None:
                    raise ProductionActivityError(
                        "live final-master transcription evidence is not configured"
                    )
                evidence = ProviderEvidence(
                    operation="transcribe",
                    provider=qa.transcript.provider,
                    request_id=qa.transcript.request_id,
                    model=qa.transcript.model,
                    input_sha256=qa.master_checksum,
                    output_sha256=sha256(
                        qa.transcript.text.encode("utf-8")
                    ).hexdigest(),
                    usage=provider_usage_to_mapping(qa.transcript.usage),
                    currency="USD",
                    estimated_cost=qa.transcript.cost,
                    reconciled_cost=None,
                    latency_ms=max(
                        0, (perf_counter_ns() - started_ns + 999_999) // 1_000_000
                    ),
                    retry_count=0,
                    occurred_at=datetime.now(UTC),
                    evidence_kind="provider-live",
                )
                try:
                    evidence_factory(production)(evidence)
                except Exception as error:
                    raise ProductionActivityError(
                        "live final-master transcription evidence could not be "
                        "persisted"
                    ) from error
            dependencies.stages.save_qa(key, qa)
            return {
                "episode_id": production.episode_id,
                "episode_version_id": production.episode_version_id,
                "passed": qa.passed,
                "qa_master_checksum": qa.master_checksum,
                "qa_critical_token_accuracy": qa.critical_token_accuracy,
                "stage_record_key": key,
            }
        except ProductionActivityError as error:
            raise ApplicationError(
                "final-master transcription activity rejected",
                type="ProductionStageOutputRejected",
                non_retryable=True,
            ) from error
        except Exception as error:
            raise ApplicationError(
                "final-master transcription activity rejected",
                type="ProductionStageOutputRejected",
                non_retryable=True,
            ) from error

    return final_master


def _show_notes(prepared: ContentPreparationResult) -> tuple[str, ...]:
    notes = tuple(
        turn.text.strip()
        for turn in prepared.script.turns
        if turn.text.strip() and turn.text.strip() in prepared.snapshot.source
    )
    if notes:
        return notes[:2]
    for block in prepared.snapshot.blocks:
        if block.text.strip():
            return (block.text.strip(),)
    raise ProductionActivityError("source-bound show notes are unavailable")


def build_package_activity(
    dependencies: ProductionActivityDependencies,
) -> Callable[..., object]:
    """Build the immutable package generation and commit activity."""

    @activity.defn(name=PACKAGE_EPISODE_ACTIVITY_NAME)
    def package(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            production = _production_input(payload.get("production_input"))
            master_payload = payload.get("master")
            if not isinstance(master_payload, Mapping):
                raise ProductionActivityError("master output is malformed")
            key = master_payload.get("stage_record_key")
            if not isinstance(key, str) or key != _stage_key(production):
                raise ProductionActivityError("package master identity is invalid")
            master = dependencies.stages.load_master(key)
            qa = dependencies.stages.load_qa(key)
            prepared = dependencies.prepared_content(production)
            try:
                version_id = UUID(production.episode_version_id)
            except ValueError as error:
                raise ProductionActivityError(
                    "package episode version ID must be UUIDv7"
                ) from error
            preparation = payload.get("preparation")
            handoff = payload.get("handoff")
            if not isinstance(preparation, Mapping) or not isinstance(handoff, Mapping):
                raise ProductionActivityError("package evidence handoff is malformed")
            content_manifest = dict(prepared.manifest)
            content_manifest["show_notes"] = _show_notes(prepared)
            render_evidence = {
                "workflow_id": _stage_key(production),
                "selected_candidate_ids": list(
                    cast(list[object], handoff.get("selected_candidate_ids", []))
                ),
                "render_input_sha256": handoff["render_input_sha256"],
                "render_result_sha256": handoff["render_result_sha256"],
            }
            package_input = PackageGenerationInput(
                version_id,
                prepared.snapshot,
                prepared.profile,
                prepared.script,
                1,
                prepared.segments,
                content_manifest,
                master,
                qa,
                render_evidence,
                dependencies.renderer_identity,
                {"preparation_manifest_sha256": prepared.manifest_sha256},
            )
            artifacts = generate_package_artifacts(package_input)
            package = dependencies.packages.commit(
                version_id, artifacts, build_package_provenance(package_input)
            )
            package_sha256 = package_sha256_for(package, dependencies.package_artifacts)
            package_manifest_sha256 = manifest_sha256_for(package)
            if dependencies.package_completion_recorder is not None:
                try:
                    dependencies.package_completion_recorder(
                        production,
                        qa,
                        package_sha256,
                        package_manifest_sha256,
                    )
                except Exception as error:
                    raise ProductionActivityError(
                        "durable episode package completion could not be recorded"
                    ) from error
            return {
                "episode_id": production.episode_id,
                "episode_version_id": production.episode_version_id,
                "passed": True,
                "package_sha256": package_sha256,
                "package_manifest_sha256": package_manifest_sha256,
            }
        except ProductionActivityError as error:
            raise ApplicationError(
                "package activity rejected",
                type="ProductionStageOutputRejected",
                non_retryable=True,
            ) from error
        except Exception as error:
            raise ApplicationError(
                "package activity rejected",
                type="ProductionStageOutputRejected",
                non_retryable=True,
            ) from error

    return package


def build_production_activities(
    dependencies: ProductionActivityDependencies,
    request_factory: PreparationRequestFactory,
    render_snapshot_factory: RenderSnapshotFactory | None = None,
    live_adaptation_factory: LiveAdaptationFactory | None = None,
    prepared_content_recorder: PreparedContentRecorder | None = None,
) -> tuple[Callable[..., object], ...]:
    """Build source validation, preparation, master, QA, and package activities."""
    return (
        build_validate_source_activity(),
        build_json_prepare_content_activity(
            request_factory,
            render_snapshot_factory,
            live_adaptation_factory,
            prepared_content_recorder,
        ),
        build_master_activity(dependencies),
        build_final_master_transcription_activity(dependencies),
        build_package_activity(dependencies),
    )


__all__ = [
    "PreparedContentResolver",
    "ProductionActivityDependencies",
    "ProductionActivityError",
    "ProductionStageStore",
    "ScriptDerivedTranscriber",
    "TranscriberFactory",
    "build_final_master_transcription_activity",
    "build_master_activity",
    "build_package_activity",
    "build_production_activities",
    "spoken_text_for_prepared",
]
