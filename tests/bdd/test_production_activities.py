"""Acceptance coverage for durable production worker activities."""

from __future__ import annotations

import asyncio
import wave
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.artifacts import FilesystemArtifactStore
from poddown.audio.diagnostics import diagnose_wav
from poddown.audio.mastering import (
    MasteredAudio,
    MasteringProvenance,
    MasteringService,
)
from poddown.audio.production_activities import (
    ProductionActivityDependencies,
    ProductionStageStore,
    ScriptDerivedTranscriber,
    build_final_master_transcription_activity,
    build_package_activity,
    spoken_text_for_prepared,
)
from poddown.audio.production_workflow import ProductionWorkflowInput
from poddown.audio.storage import (
    FilesystemArtifactStore as RenderArtifactStore,
)
from poddown.audio.storage import (
    FilesystemRenderRecordStore,
)
from poddown.content.adaptation import (
    AdaptationProposal,
    EpisodeTreatment,
    FixtureReasoningPort,
)
from poddown.content.lexicon import PronunciationEntry, PronunciationLexicon
from poddown.content.models import (
    ScriptTurn,
    SourceAnchor,
    VoiceAsset,
    VoiceConsent,
)
from poddown.content.segmentation import SegmentationCapabilities
from poddown.content.service import (
    ContentPreparationRequest,
    ContentPreparationResult,
    prepare_content,
)
from poddown.content.source import snapshot_source
from poddown.domain import FidelityResult, ProviderUsage
from poddown.packages import (
    EpisodePackageService,
    manifest_sha256_for,
    package_sha256_for,
)
from poddown.providers.contracts import TranscriptResult, TranscriptWord
from poddown.qa.final_master import FinalMasterGate, FinalMasterQaResult

scenarios("../features/production_activities.feature")

SOURCE = "LiDAR remains source-bound.\n"
PROFILE_YAML = """\
profile_id: narration
version: v1
format_type: narration
target_minutes: 1
speakers:
  - speaker_id: host
    display_name: Host
    voice_asset_id: voice-host
"""
EPISODE_VERSION_ID = UUID("123e4567-e89b-12d3-a456-426614174000")


def _request() -> ContentPreparationRequest:
    """Build the smallest source-bound preparation request accepted by the service."""
    snapshot = snapshot_source(SOURCE)
    block = snapshot.blocks[0]
    anchor = SourceAnchor(block.block_id, block.start, block.end)
    treatment = EpisodeTreatment(
        "treatment-1",
        "narration",
        ("source-bound",),
        1,
        ("reference",),
        {"host": "presenter"},
        (anchor,),
        ("turn-1",),
    )
    turns = (
        ScriptTurn(
            "turn-1",
            "host",
            SOURCE.strip(),
            "factual",
            (anchor,),
            (anchor,),
        ),
    )
    return ContentPreparationRequest(
        markdown=SOURCE,
        profile_yaml=PROFILE_YAML,
        treatment=treatment,
        reasoning=FixtureReasoningPort(
            {snapshot.source_sha256: AdaptationProposal(treatment, turns)}, {}
        ),
        lexicon_layers={
            "episode": PronunciationLexicon(
                "episode",
                "v1",
                (
                    PronunciationEntry(
                        "lidar",
                        "LiDAR",
                        "LIE-dar",
                        "v1",
                        category="technical_term",
                    ),
                ),
            )
        },
        capabilities=SegmentationCapabilities(100, None, frozenset({"host"})),
        voice_assets=(VoiceAsset("voice-host", True),),
        consents=(VoiceConsent("voice-host", True),),
    )


def _prepared() -> ContentPreparationResult:
    return prepare_content(_request())


def _production(prepared: ContentPreparationResult) -> ProductionWorkflowInput:
    return ProductionWorkflowInput(
        episode_id="episode-1",
        episode_version_id=str(EPISODE_VERSION_ID),
        source_sha256=prepared.snapshot.source_sha256,
        profile_id=prepared.profile.profile_id,
        prepared_content_reference={"prepared": prepared.manifest_sha256},
        execution_mode="deterministic-local",
    )


def _wav_bytes() -> bytes:
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44_100)
        output.writeframes(
            b"".join((1000).to_bytes(2, "little", signed=True) for _ in range(882))
        )
    return stream.getvalue()


def _master(prepared: ContentPreparationResult) -> MasteredAudio:
    wav = _wav_bytes()
    mp3 = b"ID3-deterministic-master"
    diagnostics = diagnose_wav(
        wav,
        expected_sample_rate_hz=44_100,
        expected_channels=1,
        min_duration_seconds=0.01,
        max_duration_seconds=10.0,
    )
    return MasteredAudio(
        "episode-1",
        str(EPISODE_VERSION_ID),
        wav,
        mp3,
        diagnostics,
        MasteringProvenance(
            profile_version=prepared.profile.version,
            ffmpeg_executable="fake-ffmpeg",
            ffmpeg_version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("loudnorm",),
            input_checksums=("a" * 64,),
            wav_checksum=sha256(wav).hexdigest(),
            mp3_checksum=sha256(mp3).hexdigest(),
            wav_metadata=diagnostics.to_dict(),
            mp3_metadata={"codec_name": "mp3"},
            commands=(("fake-ffmpeg", "-i", "input.wav"),),
        ),
    )


def _qa(
    prepared: ContentPreparationResult, master: MasteredAudio
) -> FinalMasterQaResult:
    text = spoken_text_for_prepared(prepared)
    words = tuple(
        TranscriptWord(word, index * 0.1, (index + 1) * 0.1)
        for index, word in enumerate(text.split())
    )
    checksum = sha256(master.wav_bytes).hexdigest()
    transcript = TranscriptResult(
        text=text,
        words=words,
        provider="script-derived",
        model="test-script-v1",
        usage=ProviderUsage(len(master.wav_bytes), len(words)),
        request_id="test-final-master-1",
        checksum=checksum,
        mode="deterministic-local",
    )
    fidelity = FidelityResult(True, 1.0, "none")
    return FinalMasterQaResult(
        subject_id=master.episode_id,
        master_checksum=checksum,
        transcript=transcript,
        fidelity=fidelity,
        gates=(
            FinalMasterGate("audio", True, {"passed": True}),
            FinalMasterGate("critical_tokens", True, {"accuracy": 1.0}),
        ),
        diagnostics=master.diagnostics,
    )


def _dependencies(
    root: Path,
    prepared: ContentPreparationResult,
    stages: ProductionStageStore,
    package_artifacts: FilesystemArtifactStore,
) -> ProductionActivityDependencies:
    return ProductionActivityDependencies(
        render_artifacts=RenderArtifactStore(root / "render-artifacts"),
        render_records=FilesystemRenderRecordStore(root / "render-records"),
        stages=stages,
        packages=EpisodePackageService(package_artifacts, root / "packages"),
        package_artifacts=package_artifacts,
        prepared_content=lambda _production: prepared,
        mastering=MasteringService(),
        transcriber_factory=lambda _production, prepared: ScriptDerivedTranscriber(
            spoken_text_for_prepared(prepared),
            provider="script-derived",
            model="test-script-v1",
            mode="deterministic-local",
        ),
        renderer_identity="test-renderer-v1",
    )


@given("a durable production stage fixture")
def durable_stage_fixture(context: dict[str, Any], tmp_path: Path) -> None:
    prepared = _prepared()
    production = _production(prepared)
    master = _master(prepared)
    qa = _qa(prepared, master)
    stages = ProductionStageStore(tmp_path / "stages")
    key = f"episode-production-{production.digest()}"
    stages.save_master(key, master)
    stages.save_qa(key, qa)
    context.values.update({"stages": stages, "key": key, "master": master, "qa": qa})


@when("the persisted production stage evidence is replayed")
def replay_stage_evidence(context: dict[str, Any]) -> None:
    stages: ProductionStageStore = context.values["stages"]
    key: str = context.values["key"]
    context.values["loaded_master"] = stages.load_master(key)
    context.values["loaded_qa"] = stages.load_qa(key)


@then("the master and final-master QA evidence is identical")
def assert_replayed_stage_evidence(context: dict[str, Any]) -> None:
    assert context.values["loaded_master"] == context.values["master"]
    assert context.values["loaded_qa"] == context.values["qa"]


@given("a durable production package activity fixture")
def durable_package_fixture(context: dict[str, Any], tmp_path: Path) -> None:
    prepared = _prepared()
    production = _production(prepared)
    master = _master(prepared)
    stages = ProductionStageStore(tmp_path / "stages")
    key = f"episode-production-{production.digest()}"
    stages.save_master(key, master)
    stages.save_qa(key, _qa(prepared, master))
    package_artifacts = FilesystemArtifactStore(tmp_path / "package-artifacts")
    context.values.update(
        {
            "production": production,
            "stages": stages,
            "key": key,
            "package_artifacts": package_artifacts,
            "dependencies": _dependencies(
                tmp_path, prepared, stages, package_artifacts
            ),
        }
    )


@when("the durable package activity runs")
def run_package_activity(context: dict[str, Any]) -> None:
    production: ProductionWorkflowInput = context.values["production"]
    dependencies: ProductionActivityDependencies = context.values["dependencies"]
    result = build_package_activity(dependencies)(
        {
            "production_input": production.to_dict(),
            "preparation": {"manifest_sha256": "a" * 64},
            "handoff": {
                "selected_candidate_ids": ["candidate-1"],
                "render_input_sha256": "b" * 64,
                "render_result_sha256": "c" * 64,
            },
            "master": {
                "stage_record_key": context.values["key"],
            },
        }
    )
    context.values["package_result"] = result
    package = dependencies.packages.get(production.episode_version_id)
    assert package is not None
    context.values["package"] = package


@then("it returns separate package and manifest digests")
def assert_package_digests(context: dict[str, Any]) -> None:
    result = context.values["package_result"]
    package = context.values["package"]
    package_artifacts: FilesystemArtifactStore = context.values["package_artifacts"]
    assert result["package_sha256"] == package_sha256_for(package, package_artifacts)
    assert result["package_manifest_sha256"] == manifest_sha256_for(package)
    assert result["package_sha256"] != package.provenance.final_sha256


def test_package_activity_records_the_verified_completion(tmp_path: Path) -> None:
    """Package completion is handed to the durable lifecycle after verification."""
    prepared = _prepared()
    production = _production(prepared)
    master = _master(prepared)
    qa = _qa(prepared, master)
    stages = ProductionStageStore(tmp_path / "stages")
    key = f"episode-production-{production.digest()}"
    stages.save_master(key, master)
    stages.save_qa(key, qa)
    package_artifacts = FilesystemArtifactStore(tmp_path / "package-artifacts")
    calls: list[tuple[ProductionWorkflowInput, FinalMasterQaResult, str, str]] = []
    dependencies = replace(
        _dependencies(tmp_path, prepared, stages, package_artifacts),
        package_completion_recorder=lambda value, result, package_sha, manifest_sha: calls.append(
            (value, result, package_sha, manifest_sha)
        ),
    )

    result = build_package_activity(dependencies)(
        {
            "production_input": production.to_dict(),
            "preparation": {"manifest_sha256": "a" * 64},
            "handoff": {
                "selected_candidate_ids": ["candidate-1"],
                "render_input_sha256": "b" * 64,
                "render_result_sha256": "c" * 64,
            },
            "master": {"stage_record_key": key},
        }
    )

    assert calls == [
        (
            production,
            qa,
            result["package_sha256"],
            result["package_manifest_sha256"],
        )
    ]


def test_production_stage_replay_rejects_conflicting_master(tmp_path: Path) -> None:
    """Immutability must reject a second master for the same production snapshot."""
    prepared = _prepared()
    production = _production(prepared)
    stages = ProductionStageStore(tmp_path / "stages")
    key = f"episode-production-{production.digest()}"
    master = _master(prepared)
    stages.save_master(key, master)

    with pytest.raises(RuntimeError, match="conflicts"):
        stages.save_master(key, replace(master, episode_id="another-episode"))


def test_final_master_activity_persists_checksum_bound_qa(tmp_path: Path) -> None:
    """The worker must persist QA only after the transcriber is bound to the master."""
    prepared = _prepared()
    production = _production(prepared)
    master = _master(prepared)
    stages = ProductionStageStore(tmp_path / "stages")
    key = f"episode-production-{production.digest()}"
    stages.save_master(key, master)
    package_artifacts = FilesystemArtifactStore(tmp_path / "package-artifacts")
    dependencies = _dependencies(tmp_path, prepared, stages, package_artifacts)
    activity_fn = build_final_master_transcription_activity(
        replace(
            dependencies,
            transcriber_factory=lambda _production, prepared: _ExpectedTranscriber(
                spoken_text_for_prepared(prepared)
            ),
        )
    )

    result = asyncio.run(
        activity_fn(
            {
                "production_input": production.to_dict(),
                "master": {"stage_record_key": key},
            }
        )
    )

    assert result["passed"] is True
    assert stages.load_qa(key).master_checksum == sha256(master.wav_bytes).hexdigest()


def test_live_final_master_activity_records_provider_evidence(tmp_path: Path) -> None:
    """Live final-master ASR must persist evidence before the stage completes."""
    prepared = _prepared()
    production = replace(_production(prepared), execution_mode="live-provider")
    master = _master(prepared)
    stages = ProductionStageStore(tmp_path / "stages")
    key = f"episode-production-{production.digest()}"
    stages.save_master(key, master)
    package_artifacts = FilesystemArtifactStore(tmp_path / "package-artifacts")
    recorded: list[object] = []
    dependencies = replace(
        _dependencies(tmp_path, prepared, stages, package_artifacts),
        provider_evidence_recorder_factory=lambda _production: recorded.append,
    )

    result = asyncio.run(
        build_final_master_transcription_activity(dependencies)(
            {
                "production_input": production.to_dict(),
                "master": {"stage_record_key": key},
            }
        )
    )

    assert result["passed"] is True
    assert len(recorded) == 1
    evidence = recorded[0]
    assert evidence.operation == "transcribe"
    assert evidence.evidence_kind == "provider-live"
    assert evidence.input_sha256 == sha256(master.wav_bytes).hexdigest()


class _ExpectedTranscriber:
    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        words = tuple(
            TranscriptWord(word, index * 0.1, (index + 1) * 0.1)
            for index, word in enumerate(self._text.split())
        )
        return TranscriptResult(
            text=self._text,
            words=words,
            provider="script-derived",
            model="test-script-v1",
            usage=ProviderUsage(len(audio), len(words)),
            request_id="test-final-master-1",
            checksum=sha256(audio).hexdigest(),
            mode="deterministic-local",
        )
