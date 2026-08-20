"""Temporal integration evidence for the complete local production workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.audio.activities import (
    build_durable_render_activity,
    deterministic_quality_evaluator,
)
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.prepare_activity import PREPARE_CONTENT_ACTIVITY_NAME
from poddown.audio.production_workflow import (
    FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME,
    MASTER_EPISODE_ACTIVITY_NAME,
    PACKAGE_EPISODE_ACTIVITY_NAME,
    EpisodeProductionWorkflow,
    ProductionWorkflowInput,
    build_validate_source_activity,
    workflow_id_for,
)
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemRenderRecordStore,
)
from poddown.audio.workflow import (
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    RenderRequest,
    SegmentWorkflowInput,
)
from tests.temporal_support import retry_local_temporal_environment

SOURCE = "A source-bound production workflow.\n"
SOURCE_SHA256 = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
EPISODE_ID = "episode-production-temporal-001"
VERSION_ID = "version-production-temporal-001"
PROFILE_ID = "technical-dialogue"
TASK_QUEUE = "poddown-production-workflow-integration"


def _production_input() -> ProductionWorkflowInput:
    """Build the smallest deterministic production snapshot accepted by the workflow."""
    segment_id = "segment-production-temporal-001"
    render_input = EpisodeWorkflowInput(
        episode_id=EPISODE_ID,
        episode_version=VERSION_ID,
        max_attempts=1,
        segments=(
            SegmentWorkflowInput(
                segment_id=segment_id,
                render_request=RenderRequest(
                    episode_id=EPISODE_ID,
                    episode_version=VERSION_ID,
                    segment_id=segment_id,
                    speaker_id="host",
                    expected_spoken_text="A source-bound production workflow.",
                    voice_asset_id="voice-host-v1",
                    provider="local",
                    model="local-deterministic-v1",
                ),
                consent=VoiceConsent(
                    "voice-host-v1",
                    "consent-production-temporal-001",
                    frozenset({"local"}),
                ),
                critical_tokens=("source-bound",),
            ),
        ),
    )
    return ProductionWorkflowInput(
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        source_sha256=SOURCE_SHA256,
        profile_id=PROFILE_ID,
        prepared_content_reference={
            "episode_render_workflow_input_json": render_input.to_json(),
            "preparation_activity_input": {
                "tenant_id": "tenant-production-temporal-001",
                "project_id": "project-production-temporal-001",
                "episode_id": EPISODE_ID,
                "source_markdown": SOURCE,
                "profile_id": PROFILE_ID,
            },
        },
        execution_mode="deterministic-local",
    )


def test_complete_production_workflow_crosses_temporal_child_and_activity_wires(
    tmp_path,
) -> None:
    """The local workflow executes validation, child, and stage boundaries."""

    async def run_workflow() -> str:
        production = _production_input()
        artifacts = FilesystemArtifactStore(tmp_path / "render-artifacts")
        records = FilesystemRenderRecordStore(tmp_path / "render-records", artifacts)
        render_activity = build_durable_render_activity(
            DurableRenderService(artifacts, records),
            DeterministicLocalRenderer(),
            artifacts,
            quality_evaluator=deterministic_quality_evaluator,
        )
        validate_activity = build_validate_source_activity()

        @activity.defn(name=PREPARE_CONTENT_ACTIVITY_NAME)
        async def prepare(payload: dict[str, Any]) -> dict[str, Any]:
            assert payload["source_markdown"] == SOURCE
            return {
                "episode_id": EPISODE_ID,
                "episode_version_id": VERSION_ID,
                "source_sha256": SOURCE_SHA256,
                "resolved_profile_id": PROFILE_ID,
                "resolved_profile_version": "v1",
                "manifest_sha256": "a" * 64,
            }

        @activity.defn(name=MASTER_EPISODE_ACTIVITY_NAME)
        async def master(payload: dict[str, Any]) -> dict[str, Any]:
            assert payload["episode_id"] == EPISODE_ID
            return {
                "episode_id": EPISODE_ID,
                "episode_version_id": VERSION_ID,
                "passed": True,
                "master_wav_checksum": "b" * 64,
                "master_mp3_checksum": "c" * 64,
            }

        @activity.defn(name=FINAL_MASTER_TRANSCRIPTION_ACTIVITY_NAME)
        async def final_master(payload: dict[str, Any]) -> dict[str, Any]:
            master_payload = payload["master"]
            assert master_payload["master_wav_checksum"] == "b" * 64
            return {
                "episode_id": EPISODE_ID,
                "episode_version_id": VERSION_ID,
                "passed": True,
                "qa_master_checksum": "b" * 64,
                "qa_critical_token_accuracy": 1.0,
            }

        @activity.defn(name=PACKAGE_EPISODE_ACTIVITY_NAME)
        async def package(payload: dict[str, Any]) -> dict[str, Any]:
            assert payload["qa"]["qa_critical_token_accuracy"] == 1.0
            return {
                "episode_id": EPISODE_ID,
                "episode_version_id": VERSION_ID,
                "passed": True,
                "package_sha256": "d" * 64,
                "package_manifest_sha256": "e" * 64,
            }

        with ThreadPoolExecutor(max_workers=1) as activity_executor:
            async with (
                retry_local_temporal_environment(
                    WorkflowEnvironment.start_local
                ) as environment,
                Worker(
                    environment.client,
                    task_queue=TASK_QUEUE,
                    workflows=[EpisodeProductionWorkflow, EpisodeRenderWorkflow],
                    activities=[
                        validate_activity,
                        prepare,
                        render_activity,
                        master,
                        final_master,
                        package,
                    ],
                    activity_executor=activity_executor,
                ),
            ):
                result = await environment.client.execute_workflow(
                    EpisodeProductionWorkflow.run,
                    production.to_json(),
                    id=workflow_id_for(production),
                    task_queue=TASK_QUEUE,
                )
                return result

    result = json.loads(asyncio.run(run_workflow()))

    assert result == {
        "workflow_id": f"episode-production-{_production_input().digest()}",
        "status": "completed",
        "stage": "packaged",
        "package_sha256": "d" * 64,
        "package_manifest_sha256": "e" * 64,
        "publication": None,
    }
