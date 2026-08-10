"""Provider-neutral Temporal activities for durable audio rendering."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from inspect import isawaitable
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
from poddown.audio.storage import (
    ArtifactIntegrityError,
    FilesystemArtifactStore,
    FilesystemQualityRecordStore,
    FilesystemTranscriptionRecordStore,
    IdempotencyConflictError,
)
from poddown.audio.workflow import (
    RENDER_SEGMENT_ACTIVITY_NAME,
    EpisodeWorkflowInput,
    MalformedAudioError,
    RightsFailureError,
    SegmentWorkflowInput,
    TranscriptionFailureError,
    TranscriptionTransientError,
    WorkflowContractError,
    activity_key_for,
)
from poddown.domain import ProviderUsage
from poddown.providers.contracts import Transcriber, TranscriptResult
from poddown.providers.http import ProviderRateLimited, ProviderRequestFailed
from poddown.qa.fidelity import evaluate_critical_tokens

type QualityEvaluator = Callable[
    [RenderRequest, bytes, AudioDiagnostics, tuple[str, ...]],
    CandidateQuality | Awaitable[CandidateQuality],
]
type ActivityHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _quality_cache_key(request: RenderRequest, critical_tokens: tuple[str, ...]) -> str:
    """Bind cached quality evidence to the ordered QA token inputs."""
    payload = json.dumps(
        {"candidate_id": request.candidate_id, "critical_tokens": critical_tokens},
        separators=(",", ":"),
    )
    return f"candidate-{sha256(payload.encode()).hexdigest()}"


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
        transcription=TranscriptResult(
            text=request.expected_spoken_text,
            words=(),
            provider="local",
            model="deterministic-transcription-v1",
            usage=ProviderUsage(
                input_units=len(request.expected_spoken_text),
                output_units=len(audio_bytes),
            ),
            request_id=f"local-transcription-{request.candidate_id[10:22]}",
            checksum=sha256(audio_bytes).hexdigest(),
            cost=Decimal("0"),
            mode="deterministic-local",
        ),
    )


def build_transcription_quality_evaluator(
    transcriber: Transcriber,
    *,
    transcription_records: FilesystemTranscriptionRecordStore | None = None,
) -> QualityEvaluator:
    """Build an evaluator that uses and durably records provider evidence."""
    if not hasattr(transcriber, "transcribe"):
        raise TypeError("transcriber must expose transcribe(audio)")

    async def evaluate(
        request: RenderRequest,
        audio_bytes: bytes,
        diagnostics: AudioDiagnostics,
        critical_tokens: tuple[str, ...],
    ) -> CandidateQuality:
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise TranscriptionFailureError("transcription requires audio bytes")
        audio_checksum = sha256(audio_bytes).hexdigest()
        persisted = None
        if transcription_records is not None:
            try:
                persisted = transcription_records.find(request.candidate_id)
            except ArtifactIntegrityError as error:
                raise TranscriptionFailureError(
                    "persisted transcription evidence is invalid"
                ) from error
        if persisted is not None:
            result = persisted.result
        else:
            try:
                result = await transcriber.transcribe(audio_bytes)
            except (ProviderRateLimited, TimeoutError) as error:
                raise TranscriptionTransientError(
                    "transcription provider temporarily unavailable"
                ) from error
            except (ProviderRequestFailed, ValueError, TypeError) as error:
                raise TranscriptionFailureError(
                    "transcription provider returned invalid evidence"
                ) from error
            except TranscriptionTransientError:
                raise
            except Exception as error:
                raise TranscriptionFailureError(
                    "transcription provider failed"
                ) from error
            if not isinstance(result, TranscriptResult):
                raise TranscriptionFailureError(
                    "transcriber returned malformed evidence"
                )
        if not result.text.strip():
            raise TranscriptionFailureError("transcriber returned empty text")
        if result.checksum != audio_checksum:
            raise TranscriptionFailureError(
                "transcript checksum does not match audio bytes"
            )
        if persisted is None and transcription_records is not None:
            try:
                persisted = transcription_records.save(request.candidate_id, result)
            except (ArtifactIntegrityError, IdempotencyConflictError) as error:
                raise TranscriptionFailureError(
                    "transcription evidence could not be persisted"
                ) from error
            result = persisted.result
        fidelity = evaluate_critical_tokens(critical_tokens, result.text)
        return CandidateQuality(
            candidate_id=request.candidate_id,
            fidelity=fidelity,
            diagnostics=diagnostics,
            pronunciation_passed=True,
            soft_score=Decimal("0"),
            transcription=result,
        )

    return evaluate


def build_durable_render_activity(
    service: DurableRenderService,
    renderer: AudioRenderer,
    artifacts: FilesystemArtifactStore,
    *,
    quality_evaluator: QualityEvaluator | None = None,
    transcriber: Transcriber | None = None,
    quality_records: FilesystemQualityRecordStore | None = None,
    transcription_records: FilesystemTranscriptionRecordStore | None = None,
) -> ActivityHandler:
    """Build a Temporal activity; non-local requests require explicit QA."""
    if not isinstance(service, DurableRenderService):
        raise TypeError("service must be DurableRenderService")
    if not isinstance(artifacts, FilesystemArtifactStore):
        raise TypeError("artifacts must be FilesystemArtifactStore")
    if quality_evaluator is not None and transcriber is not None:
        raise TypeError("provide quality_evaluator or transcriber, not both")
    if transcriber is not None and (
        quality_records is None or transcription_records is None
    ):
        raise TypeError(
            "provider-backed activity requires quality and transcription records"
        )
    evaluator = quality_evaluator or (
        build_transcription_quality_evaluator(
            transcriber, transcription_records=transcription_records
        )
        if transcriber is not None
        else None
    )

    @activity.defn(name=RENDER_SEGMENT_ACTIVITY_NAME)
    async def render_segment(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await _run_render_activity(
                payload,
                service=service,
                renderer=renderer,
                artifacts=artifacts,
                quality_evaluator=evaluator,
                quality_records=quality_records,
            )
        except RightsDeniedError as error:
            raise ApplicationError(
                str(error),
                type=RightsFailureError.__name__,
                non_retryable=True,
            ) from error
        except TranscriptionFailureError as error:
            raise ApplicationError(
                str(error),
                type=TranscriptionFailureError.__name__,
                non_retryable=True,
            ) from error
        except TranscriptionTransientError as error:
            raise ApplicationError(
                str(error),
                type=TranscriptionTransientError.__name__,
                non_retryable=False,
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
    quality_records: FilesystemQualityRecordStore | None,
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
    service.preflight(request, segment.consent, renderer)
    if quality_records is not None:
        quality_key = _quality_cache_key(request, segment.critical_tokens)
        persisted_quality = quality_records.find(
            quality_key, expected_candidate_id=request.candidate_id
        )
        if persisted_quality is not None:
            return persisted_quality.to_dict()
    outcomes = await service.render_takes(
        request,
        segment.consent,
        renderer,
        take_count=1,
    )
    if len(outcomes) != 1:
        raise WorkflowContractError("render activity must produce one take")
    outcome = outcomes[0]
    if quality_records is not None:
        persisted_quality = quality_records.find(
            quality_key, expected_candidate_id=request.candidate_id
        )
        if persisted_quality is not None:
            return persisted_quality.to_dict()
    audio_bytes = artifacts.read(outcome.candidate.artifact)
    diagnostics = diagnose_wav(
        audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )
    quality_value = evaluator(
        request,
        audio_bytes,
        diagnostics,
        segment.critical_tokens,
    )
    quality = await quality_value if isawaitable(quality_value) else quality_value
    if quality.candidate_id != outcome.candidate.candidate_id:
        raise WorkflowContractError("quality candidate does not match render candidate")
    if quality_records is not None:
        quality_records.save(quality, cache_key=quality_key)
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
    "build_transcription_quality_evaluator",
    "deterministic_quality_evaluator",
]
