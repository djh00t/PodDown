"""Integration evidence for the Temporal-backed local orchestration boundary."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.audio.contracts import RenderRequest
from poddown.audio.diagnostics import AudioDiagnostics
from poddown.audio.orchestration import TemporalEpisodeWorkflowService
from poddown.audio.rights import VoiceConsent
from poddown.audio.selection import CandidateQuality
from poddown.audio.workflow import (
    RENDER_SEGMENT_ACTIVITY_NAME,
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    SegmentWorkflowInput,
)
from poddown.domain import FidelityResult
from tests.temporal_support import retry_local_temporal_environment

TASK_QUEUE = "poddown-temporal-orchestration-tests"


def _episode_input() -> EpisodeWorkflowInput:
    """Return one immutable two-segment local demo snapshot."""
    segments: list[SegmentWorkflowInput] = []
    for index in (1, 2):
        segment_id = f"segment-{index}"
        text = f"Deterministic orchestration for {segment_id}."
        voice_asset_id = "voice-host-v1" if index == 1 else "voice-guest-v1"
        segments.append(
            SegmentWorkflowInput(
                segment_id=segment_id,
                render_request=RenderRequest(
                    episode_id="temporal-demo-episode",
                    episode_version="v1",
                    segment_id=segment_id,
                    speaker_id="host" if index == 1 else "guest",
                    expected_spoken_text=text,
                    voice_asset_id=voice_asset_id,
                    provider="local",
                    model="local-deterministic-v1",
                ),
                consent=VoiceConsent(
                    voice_asset_id,
                    "temporal-consent-1",
                    frozenset({"local"}),
                ),
                critical_tokens=(segment_id,),
            )
        )
    return EpisodeWorkflowInput(
        episode_id="temporal-demo-episode",
        episode_version="v1",
        segments=tuple(segments),
        max_attempts=2,
    )


def _quality(candidate_id: str, *, passed: bool = True) -> dict[str, object]:
    """Return one JSON-safe quality result for a deterministic activity."""
    return CandidateQuality(
        candidate_id=candidate_id,
        fidelity=FidelityResult(
            passed, 1.0 if passed else 0.0, "none" if passed else "segment"
        ),
        diagnostics=AudioDiagnostics(44_100, 1, 1.0, 0.5, 0.0, 0.1),
        pronunciation_passed=passed,
        soft_score=Decimal("0.50" if passed else "0.99"),
    ).to_dict()


@dataclass
class ActivityState:
    """Mutable state owned by the test activity process only."""

    calls: list[dict[str, object]] = field(default_factory=list)
    counts: dict[tuple[str, int, int], int] = field(
        default_factory=lambda: defaultdict(int)
    )


def test_temporal_retry_repair_and_completed_replay_are_idempotent():
    """Exercise retry, failed-segment repair, and replay with local Temporal."""
    asyncio.run(_run_temporal_integration())


async def _run_temporal_integration() -> None:
    state = ActivityState()

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def render_segment(payload: dict[str, Any]) -> dict[str, Any]:
        segment = payload["segment"]
        assert isinstance(segment, dict)
        segment_id = str(segment["segment_id"])
        attempt = int(payload["attempt"])
        take = int(payload["take"])
        key = (segment_id, attempt, take)
        state.counts[key] += 1
        state.calls.append(payload)
        if (
            segment_id == "segment-1"
            and attempt == 1
            and take == 0
            and state.counts[key] == 1
        ):
            raise RuntimeError("transient local activity failure")
        failed = segment_id == "segment-2" and attempt == 1
        return _quality(
            f"{segment_id}-attempt-{attempt}-take-{take}", passed=not failed
        )

    async with (
        retry_local_temporal_environment(
            WorkflowEnvironment.start_local
        ) as environment,
        Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[EpisodeRenderWorkflow],
            activities=[render_segment],
        ),
    ):
        episode_input = _episode_input()
        service = TemporalEpisodeWorkflowService(environment.client, TASK_QUEUE)
        first_result = await service.run_episode(episode_input)
        replay_result = await service.run_episode(episode_input)

    assert first_result == replay_result
    assert first_result.status == "completed"
    assert [decision.attempt for decision in first_result.decisions] == [1, 2]
    assert len(state.calls) == 4 + 3 + 3
    assert all(
        len(call["episode"]["segments"]) == 1
        for call in state.calls
        if isinstance(call.get("episode"), dict)
    )
    assert all(
        call["segment"]["segment_id"] != "segment-1" or call["attempt"] == 1
        for call in state.calls
    )
    assert state.counts[("segment-1", 1, 0)] == 2
