"""Temporal integration evidence for durable render activity replay."""

import asyncio
from dataclasses import replace
from typing import Any

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.audio.activities import build_durable_render_activity
from poddown.audio.contracts import RenderRequest
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.orchestration import TemporalEpisodeWorkflowService
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore
from poddown.audio.workflow import (
    RENDER_SEGMENT_ACTIVITY_NAME,
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    SegmentWorkflowInput,
)
from tests.temporal_support import retry_local_temporal_environment

TASK_QUEUE = "poddown-temporal-durable-render-activity-tests"


def test_temporal_retry_replays_three_durable_takes_without_new_evidence(tmp_path):
    """Retry one take without duplicating the workflow's three-take budget."""
    asyncio.run(_run_durable_render_activity(tmp_path))


async def _run_durable_render_activity(tmp_path) -> None:
    request = RenderRequest(
        episode_id="temporal-durable-render-activity",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Temporal retries durable local audio exactly once.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )
    episode_input = EpisodeWorkflowInput(
        episode_id=request.episode_id,
        episode_version=request.episode_version,
        segments=(
            SegmentWorkflowInput(
                segment_id=request.segment_id,
                render_request=request,
                consent=VoiceConsent(
                    "voice-host-v1", "consent-temporal-1", frozenset({"local"})
                ),
                critical_tokens=("Temporal", "durable", "audio"),
            ),
        ),
        max_attempts=1,
    )
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    renderer = DeterministicLocalRenderer()
    real_activity = build_durable_render_activity(
        DurableRenderService(artifacts, records), renderer, artifacts
    )
    failed_after_persistence = False

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def durable_render_with_one_post_persistence_failure(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal failed_after_persistence
        result = await real_activity(payload)
        if int(payload["take"]) == 0 and not failed_after_persistence:
            failed_after_persistence = True
            raise RuntimeError("simulated worker loss after durable persistence")
        return result

    async with (
        retry_local_temporal_environment(
            WorkflowEnvironment.start_local
        ) as environment,
        Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[EpisodeRenderWorkflow],
            activities=[durable_render_with_one_post_persistence_failure],
        ),
    ):
        service = TemporalEpisodeWorkflowService(environment.client, TASK_QUEUE)
        first_result = await service.run_episode(episode_input)
        replay_result = await service.run_episode(episode_input)

    assert first_result.status == "completed"
    assert replay_result == first_result
    expected_keys = [
        replace(request, take_index=take).idempotency_key for take in range(3)
    ]
    assert len(renderer.calls) == 3
    assert set(renderer.calls) == set(expected_keys)
    for key in expected_keys:
        record = records.find(key)
        assert record is not None
        assert record.cost_event is not None
    assert len(list((tmp_path / "records").rglob("*.json"))) == 3
