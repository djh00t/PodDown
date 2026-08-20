"""Temporal workflow evidence for terminal non-retryable activity failures."""

import asyncio
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError
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
    render_segment_activity,
)
from tests.temporal_support import retry_local_temporal_environment

TASK_QUEUE = "poddown-temporal-failure-semantics-tests"


def test_invalid_consent_terminates_workflow_without_repair_attempt(tmp_path):
    """Rights errors fail the episode at attempt one without provider usage."""
    asyncio.run(_run_invalid_consent_workflow(tmp_path))


async def _run_invalid_consent_workflow(tmp_path) -> None:
    request = RenderRequest(
        episode_id="temporal-failure-semantics",
        episode_version="v1",
        segment_id="segment-rights",
        speaker_id="host",
        expected_spoken_text="Rights must fail before provider dispatch.",
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
                    "voice-host-v1", "consent-wrong-provider", frozenset({"other"})
                ),
                critical_tokens=("Rights", "provider"),
            ),
        ),
        max_attempts=2,
    )
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    renderer = DeterministicLocalRenderer()
    handler = build_durable_render_activity(
        DurableRenderService(artifacts, records), renderer, artifacts
    )

    async with (
        retry_local_temporal_environment(
            WorkflowEnvironment.start_local
        ) as environment,
        Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[EpisodeRenderWorkflow],
            activities=[handler],
        ),
    ):
        result = await TemporalEpisodeWorkflowService(
            environment.client, TASK_QUEUE
        ).run_episode(episode_input)

    assert result.status == "failed"
    assert len(result.decisions) == 1
    assert result.decisions[0].attempt == 1
    assert result.decisions[0].failure_code == "RightsFailureError"
    assert result.terminal_failure is not None
    assert result.terminal_failure.failed_gates == ("rights",)
    assert renderer.calls == []
    assert list((tmp_path / "records").rglob("*.json")) == []


def test_unconfigured_activity_reports_configuration_gate(tmp_path):
    """The fail-closed default activity reports configuration, not QA failure."""
    asyncio.run(_run_unconfigured_activity_workflow(tmp_path))


async def _run_unconfigured_activity_workflow(tmp_path) -> None:
    del tmp_path
    request = RenderRequest(
        episode_id="temporal-unconfigured-activity",
        episode_version="v1",
        segment_id="segment-config",
        speaker_id="host",
        expected_spoken_text="The activity handler must be configured.",
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
                    "voice-host-v1", "consent-config-1", frozenset({"local"})
                ),
                critical_tokens=("activity", "configured"),
            ),
        ),
        max_attempts=1,
    )
    task_queue = "poddown-temporal-unconfigured-activity-tests"
    async with (
        retry_local_temporal_environment(
            WorkflowEnvironment.start_local
        ) as environment,
        Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[EpisodeRenderWorkflow],
            activities=[render_segment_activity],
        ),
    ):
        result = await TemporalEpisodeWorkflowService(
            environment.client, task_queue
        ).run_episode(episode_input)

    assert result.status == "failed"
    assert result.terminal_failure is not None
    assert result.terminal_failure.failed_gates == ("configuration",)
    assert result.terminal_failure.last_error_code == "ActivityNotConfigured"


def test_unknown_terminal_activity_reports_activity_gate(tmp_path):
    """Unknown non-retryable errors fail with an activity gate, not QA gates."""
    asyncio.run(_run_unknown_terminal_activity_workflow(tmp_path))


async def _run_unknown_terminal_activity_workflow(tmp_path) -> None:
    del tmp_path
    request = RenderRequest(
        episode_id="temporal-unknown-terminal-activity",
        episode_version="v1",
        segment_id="segment-unknown",
        speaker_id="host",
        expected_spoken_text="Unknown terminal activity errors fail honestly.",
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
                    "voice-host-v1", "consent-unknown-1", frozenset({"local"})
                ),
                critical_tokens=("Unknown", "activity"),
            ),
        ),
        max_attempts=2,
    )

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def fail_unknown(payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        raise ApplicationError(
            "simulated unknown terminal activity failure",
            type="UnknownTerminalFailure",
            non_retryable=True,
        )

    task_queue = "poddown-temporal-unknown-terminal-activity-tests"
    async with (
        retry_local_temporal_environment(
            WorkflowEnvironment.start_local
        ) as environment,
        Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[EpisodeRenderWorkflow],
            activities=[fail_unknown],
        ),
    ):
        result = await TemporalEpisodeWorkflowService(
            environment.client, task_queue
        ).run_episode(episode_input)

    assert result.status == "failed"
    assert result.decisions[0].attempt == 1
    assert result.decisions[0].failure_code == "UnknownTerminalFailure"
    assert result.terminal_failure is not None
    assert result.terminal_failure.failed_gates == ("activity",)
