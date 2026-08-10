"""Provider-neutral Temporal activities for durable audio rendering."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from poddown.audio.contracts import AudioRenderer, RenderRequest
from poddown.audio.diagnostics import (
    AudioDiagnostics,
    AudioDiagnosticsError,
    diagnose_wav,
)
from poddown.audio.render import DurableRenderService, RenderRejectedError
from poddown.audio.rights import RightsDeniedError
from poddown.audio.selection import CandidateQuality, CandidateSelectionError
from poddown.audio.storage import ArtifactIntegrityError, FilesystemArtifactStore
from poddown.audio.workflow import (
    RENDER_SEGMENT_ACTIVITY_NAME,
    EpisodeWorkflowInput,
    MalformedAudioError,
    RightsFailureError,
    SegmentWorkflowInput,
    WorkflowContractError,
    activity_key_for,
)
from poddown.qa.fidelity import evaluate_critical_tokens

type QualityEvaluator = Callable[
    [RenderRequest, bytes, AudioDiagnostics, tuple[str, ...]], CandidateQuality
]
type ActivityHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def deterministic_quality_evaluator(
    request: RenderRequest,
    audio_bytes: bytes,
    diagnostics: AudioDiagnostics,
    critical_tokens: tuple[str, ...],
) -> CandidateQuality:
    """Return explicit deterministic-local quality evidence for one artifact.

    The local fixture uses the expected spoken text as its deterministic
    transcript. It is a stable offline quality adapter, not evidence that a
    hosted transcription provider was called.
    """
    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise WorkflowContractError("quality evaluation requires audio bytes")
    fidelity = evaluate_critical_tokens(critical_tokens, request.expected_spoken_text)
    return CandidateQuality(
        candidate_id=request.candidate_id,
        fidelity=fidelity,
        diagnostics=diagnostics,
        pronunciation_passed=True,
        soft_score=Decimal("0"),
    )


def build_durable_render_activity(
    service: DurableRenderService,
    renderer: AudioRenderer,
    artifacts: FilesystemArtifactStore,
    *,
    quality_evaluator: QualityEvaluator | None = None,
) -> ActivityHandler:
    """Build a Temporal activity; non-local requests require explicit QA."""
    if not isinstance(service, DurableRenderService):
        raise TypeError("service must be DurableRenderService")
    if not isinstance(artifacts, FilesystemArtifactStore):
        raise TypeError("artifacts must be FilesystemArtifactStore")

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def render_segment(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await _run_render_activity(
                payload,
                service=service,
                renderer=renderer,
                artifacts=artifacts,
                quality_evaluator=quality_evaluator,
            )
        except RightsDeniedError as error:
            raise ApplicationError(
                str(error),
                type=RightsFailureError.__name__,
                non_retryable=True,
            ) from error
        except (
            ArtifactIntegrityError,
            AudioDiagnosticsError,
            RenderRejectedError,
        ) as error:
            raise ApplicationError(
                str(error),
                type=MalformedAudioError.__name__,
                non_retryable=True,
            ) from error
        except (CandidateSelectionError, WorkflowContractError) as error:
            raise ApplicationError(
                str(error),
                type=WorkflowContractError.__name__,
                non_retryable=True,
            ) from error

    return render_segment


async def _run_render_activity(
    payload: dict[str, Any],
    *,
    service: DurableRenderService,
    renderer: AudioRenderer,
    artifacts: FilesystemArtifactStore,
    quality_evaluator: QualityEvaluator | None,
) -> dict[str, Any]:
    episode_input, segment, attempt, take, activity_key = _parse_payload(payload)
    expected_key = activity_key_for(
        episode_input,
        "render",
        segment.segment_id,
        attempt=attempt,
        take=take,
    )
    if activity_key != expected_key:
        raise WorkflowContractError("activity key does not match episode snapshot")

    request = replace(segment.render_request, attempt=attempt, take_index=take)
    evaluator = quality_evaluator
    if evaluator is None:
        if request.provider != "local":
            raise WorkflowContractError(
                "non-local render requests require an explicit quality evaluator"
            )
        evaluator = deterministic_quality_evaluator
    outcomes = await service.render_takes(
        request,
        segment.consent,
        renderer,
        take_count=1,
    )
    if len(outcomes) != 1:
        raise WorkflowContractError("render activity must produce one take")
    outcome = outcomes[0]
    audio_bytes = artifacts.read(outcome.candidate.artifact)
    diagnostics = diagnose_wav(
        audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )
    quality = evaluator(
        request,
        audio_bytes,
        diagnostics,
        segment.critical_tokens,
    )
    if quality.candidate_id != outcome.candidate.candidate_id:
        raise WorkflowContractError("quality candidate does not match render candidate")
    return quality.to_dict()


def _parse_payload(
    payload: dict[str, Any],
) -> tuple[EpisodeWorkflowInput, SegmentWorkflowInput, int, int, str]:
    if not isinstance(payload, dict):
        raise WorkflowContractError("render activity payload must be a mapping")
    episode_payload = payload.get("episode")
    segment_payload = payload.get("segment")
    if not isinstance(episode_payload, dict) or not isinstance(segment_payload, dict):
        raise WorkflowContractError("render activity payload snapshots are required")
    try:
        episode_input = EpisodeWorkflowInput.from_dict(episode_payload)
        segment = SegmentWorkflowInput.from_dict(segment_payload)
    except (TypeError, ValueError) as error:
        raise WorkflowContractError(
            "render activity payload snapshot is malformed"
        ) from error
    if segment not in episode_input.segments:
        raise WorkflowContractError("activity segment is not in episode snapshot")

    attempt = payload.get("attempt")
    take = payload.get("take")
    activity_key = payload.get("activity_key")
    if type(attempt) is not int or attempt <= 0:
        raise WorkflowContractError("activity attempt must be a positive integer")
    if type(take) is not int or take < 0:
        raise WorkflowContractError("activity take must be a non-negative integer")
    if not isinstance(activity_key, str) or not activity_key:
        raise WorkflowContractError("activity key must be a non-empty string")
    return episode_input, segment, attempt, take, activity_key


__all__ = [
    "ActivityHandler",
    "QualityEvaluator",
    "build_durable_render_activity",
    "deterministic_quality_evaluator",
]
