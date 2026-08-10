"""RED contracts for the provider-bound durable render activity."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256

import pytest
from temporalio.exceptions import ApplicationError

from poddown.audio import (
    DeterministicLocalRenderer,
    DurableRenderService,
    EpisodeWorkflowInput,
    RenderRequest,
    SegmentWorkflowInput,
    VoiceConsent,
    activity_key_for,
    build_durable_render_activity,
    build_transcription_quality_evaluator,
    deterministic_quality_evaluator,
    diagnose_wav,
)
from poddown.audio.render import RenderRejectedError
from poddown.audio.selection import CandidateQuality
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemQualityRecordStore,
    FilesystemRenderRecordStore,
    FilesystemTranscriptionRecordStore,
)
from poddown.audio.workflow import (
    TranscriptionFailureError,
    TranscriptionTransientError,
)
from poddown.domain import ProviderUsage
from poddown.providers.contracts import TranscriptResult


class FixedFakeTranscriber:
    """Return deterministic transcript evidence without contacting a provider."""

    def __init__(self, *, text: str):
        self.text = text
        self.calls = 0

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        assert audio
        self.calls += 1
        return TranscriptResult(
            text=self.text,
            words=(),
            provider="fake-transcriber",
            model="fake-model-v1",
            usage=ProviderUsage(1, 1),
            request_id="fake-request-1",
            checksum=sha256(audio).hexdigest(),
            cost=Decimal("0.01"),
        )


class RaisingFakeTranscriber:
    """Raise a selected deterministic provider-side failure."""

    def __init__(self, error: Exception):
        self.error = error

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        assert audio
        raise self.error


class FailOnceQualityRecordStore(FilesystemQualityRecordStore):
    """Simulate worker loss after the transcription record is committed."""

    def __init__(self, root):
        super().__init__(root)
        self.failed = False

    def save(self, quality: CandidateQuality, *, cache_key: str | None = None) -> None:
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated quality persistence failure")
        super().save(quality, cache_key=cache_key)


def request_for(*, attempt: int = 1, take: int = 0) -> RenderRequest:
    """Build one immutable request for a specified activity attempt and take."""
    return RenderRequest(
        episode_id="episode-1",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Temporal rendering is not optional.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
        attempt=attempt,
        take_index=take,
    )


def episode_for(*, consent: VoiceConsent | None = None) -> EpisodeWorkflowInput:
    """Build the immutable episode snapshot sent across the activity boundary."""
    request = request_for()
    return EpisodeWorkflowInput(
        episode_id=request.episode_id,
        episode_version=request.episode_version,
        segments=(
            SegmentWorkflowInput(
                segment_id=request.segment_id,
                render_request=request,
                consent=consent
                or VoiceConsent("voice-host-v1", "consent-1", frozenset({"local"})),
                critical_tokens=("Temporal", "not", "optional"),
            ),
        ),
    )


def payload_for(
    episode: EpisodeWorkflowInput, *, attempt: int = 1, take: int = 0
) -> dict[str, object]:
    """Build an authenticated activity payload from an immutable snapshot."""
    segment = episode.segments[0]
    return {
        "episode": episode.to_dict(),
        "segment": segment.to_dict(),
        "attempt": attempt,
        "take": take,
        "activity_key": activity_key_for(
            episode, "render", segment.segment_id, attempt=attempt, take=take
        ),
    }


def activity_for(tmp_path, renderer: DeterministicLocalRenderer):
    """Build an activity with isolated durable stores."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    return build_durable_render_activity(
        DurableRenderService(artifacts, records), renderer, artifacts
    )


def transcription_activity_for(tmp_path, transcriber):
    """Build an activity with isolated durable stores and a fake transcriber."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    quality_records = FilesystemQualityRecordStore(tmp_path / "quality")
    transcription_records = FilesystemTranscriptionRecordStore(
        tmp_path / "transcriptions"
    )
    return build_durable_render_activity(
        DurableRenderService(artifacts, records),
        DeterministicLocalRenderer(),
        artifacts,
        transcriber=transcriber,
        quality_records=quality_records,
        transcription_records=transcription_records,
    )


def test_activity_reconstructs_request_attempt_and_take(tmp_path):
    """Catch activities that render the snapshot's default attempt or take."""
    renderer = DeterministicLocalRenderer()
    episode = episode_for()

    result = asyncio.run(
        activity_for(tmp_path, renderer)(payload_for(episode, attempt=2, take=1))
    )

    expected = request_for(attempt=2, take=1)
    assert result["candidate_id"] == expected.candidate_id
    assert renderer.calls == [expected.idempotency_key]


def test_activity_rejects_wrong_stable_activity_key_as_non_retryable(tmp_path):
    """Catch activities that trust a caller-supplied key from another snapshot."""
    renderer = DeterministicLocalRenderer()
    episode = episode_for()
    payload = payload_for(episode)
    payload["activity_key"] = "activity-for-a-different-snapshot"

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity_for(tmp_path, renderer)(payload))

    assert error.value.non_retryable is True
    assert error.value.type == "WorkflowContractError"
    assert renderer.calls == []


def test_activity_maps_mismatched_consent_before_renderer_dispatch(tmp_path):
    """Catch rights failures that reach a provider before being rejected."""
    renderer = DeterministicLocalRenderer()
    episode = episode_for(
        consent=VoiceConsent("voice-host-v1", "consent-1", frozenset({"other"}))
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity_for(tmp_path, renderer)(payload_for(episode)))

    assert error.value.non_retryable is True
    assert error.value.type == "RightsFailureError"
    assert renderer.calls == []


def test_activity_rejects_nonlocal_request_without_evaluator_before_dispatch(tmp_path):
    """Require hosted renders to provide provider-backed quality evidence."""
    renderer = DeterministicLocalRenderer()
    local_episode = episode_for()
    hosted_request = replace(
        request_for(), provider="hosted", model="hosted-renderer-v1"
    )
    hosted_segment = replace(
        local_episode.segments[0],
        render_request=hosted_request,
        consent=VoiceConsent(
            "voice-host-v1", "consent-hosted-1", frozenset({"hosted"})
        ),
    )
    episode = replace(local_episode, segments=(hosted_segment,))

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity_for(tmp_path, renderer)(payload_for(episode)))

    assert error.value.non_retryable is True
    assert error.value.type == "WorkflowContractError"
    assert renderer.calls == []


def test_deterministic_quality_evaluator_verifies_expected_critical_tokens():
    """Catch a local evaluator that omits expected-text critical-token fidelity."""
    request = request_for()
    rendered = asyncio.run(DeterministicLocalRenderer().render(request))
    diagnostics = diagnose_wav(
        rendered.audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )

    quality = deterministic_quality_evaluator(
        request,
        rendered.audio_bytes,
        diagnostics,
        ("Temporal", "not optional"),
    )

    assert quality.candidate_id == request.candidate_id
    assert quality.fidelity.passed is True
    assert quality.pronunciation_passed is True
    assert quality.diagnostics.passes_hard_gates is True


def test_transcription_quality_evaluator_passes_normalized_transcript_to_fidelity():
    """Use provider text for fidelity, including its deterministic normalization."""
    request = replace(request_for(), expected_spoken_text="canonical expected text")
    rendered = asyncio.run(DeterministicLocalRenderer().render(request))
    diagnostics = diagnose_wav(
        rendered.audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )
    transcriber = FixedFakeTranscriber(text="  NORMALIZED!  ")
    evaluator = build_transcription_quality_evaluator(transcriber)

    quality = asyncio.run(
        evaluator(request, rendered.audio_bytes, diagnostics, ("normalized",))
    )

    assert quality.fidelity.passed is True
    assert quality.transcription is not None
    assert quality.transcription.text == "  NORMALIZED!  "
    assert transcriber.calls == 1


def test_transcription_quality_evaluator_rejects_transcript_for_other_audio():
    """Never score transcript evidence that is not bound to this audio bytestring."""
    request = request_for()
    rendered = asyncio.run(DeterministicLocalRenderer().render(request))
    diagnostics = diagnose_wav(
        rendered.audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )
    transcriber = FixedFakeTranscriber(text=request.expected_spoken_text)
    result = asyncio.run(transcriber.transcribe(b"different-audio"))

    class MismatchedTranscriber:
        async def transcribe(self, audio: bytes) -> TranscriptResult:
            del audio
            return result

    evaluator = build_transcription_quality_evaluator(MismatchedTranscriber())

    with pytest.raises(TranscriptionFailureError, match="checksum"):
        asyncio.run(
            evaluator(request, rendered.audio_bytes, diagnostics, ("Temporal",))
        )


def test_transcription_quality_evaluator_rejects_empty_transcript():
    """An empty transcript cannot pass an empty critical-token configuration."""
    request = request_for()
    rendered = asyncio.run(DeterministicLocalRenderer().render(request))
    diagnostics = diagnose_wav(
        rendered.audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )
    evaluator = build_transcription_quality_evaluator(FixedFakeTranscriber(text="   "))

    with pytest.raises(TranscriptionFailureError, match="invalid evidence"):
        asyncio.run(evaluator(request, rendered.audio_bytes, diagnostics, ()))


@pytest.mark.parametrize(
    ("provider_error", "expected_type", "non_retryable"),
    [
        (ValueError("malformed response"), TranscriptionFailureError, True),
        (TimeoutError("provider timeout"), TranscriptionTransientError, False),
    ],
)
def test_activity_maps_transcription_provider_failures(
    tmp_path, provider_error, expected_type, non_retryable
):
    """Expose stable Temporal error types and retry semantics to callers."""
    activity = transcription_activity_for(
        tmp_path, RaisingFakeTranscriber(provider_error)
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(payload_for(episode_for())))

    assert error.value.type == expected_type.__name__
    assert error.value.non_retryable is non_retryable


def test_activity_rejects_quality_for_a_different_candidate(tmp_path):
    """Catch activity results whose quality evidence names another candidate."""
    renderer = DeterministicLocalRenderer()
    episode = episode_for()

    def mismatched_quality(
        request: RenderRequest,
        audio_bytes: bytes,
        diagnostics,
        critical_tokens: tuple[str, ...],
    ) -> CandidateQuality:
        return replace(
            deterministic_quality_evaluator(
                request, audio_bytes, diagnostics, critical_tokens
            ),
            candidate_id="candidate-different",
        )

    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        renderer,
        artifacts,
        quality_evaluator=mismatched_quality,
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(payload_for(episode)))

    assert error.value.non_retryable is True
    assert error.value.type == "WorkflowContractError"


def test_activity_replays_atomic_transcription_record_after_quality_failure(tmp_path):
    """Recover a committed provider response without a second transcription call."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    quality_records = FailOnceQualityRecordStore(tmp_path / "quality")
    transcription_records = FilesystemTranscriptionRecordStore(
        tmp_path / "transcriptions"
    )
    renderer = DeterministicLocalRenderer()
    transcriber = FixedFakeTranscriber(text="Temporal rendering is not optional.")
    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        renderer,
        artifacts,
        transcriber=transcriber,
        quality_records=quality_records,
        transcription_records=transcription_records,
    )
    base_episode = episode_for()
    episode = replace(
        base_episode,
        segments=(replace(base_episode.segments[0], critical_tokens=("not",)),),
    )

    with pytest.raises(RuntimeError, match="quality persistence"):
        asyncio.run(activity(payload_for(episode)))

    assert transcriber.calls == 1
    assert transcription_records.find(episode.segments[0].render_request.candidate_id)

    result = asyncio.run(activity(payload_for(episode)))

    assert CandidateQuality.from_dict(result).passes_hard_gates is True
    assert transcriber.calls == 1


def test_quality_cache_keeps_ordered_critical_tokens_in_its_identity(tmp_path):
    """A changed token order must re-evaluate QA without rerendering audio."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    quality_records = FilesystemQualityRecordStore(tmp_path / "quality")
    renderer = DeterministicLocalRenderer()
    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        renderer,
        artifacts,
        quality_records=quality_records,
    )
    first = episode_for()
    second = replace(
        first,
        segments=(replace(first.segments[0], critical_tokens=("optional", "not")),),
    )

    first_result = CandidateQuality.from_dict(asyncio.run(activity(payload_for(first))))
    second_result = CandidateQuality.from_dict(
        asyncio.run(activity(payload_for(second)))
    )

    assert first_result.candidate_id == second_result.candidate_id
    assert first_result.fidelity != second_result.fidelity
    assert len(renderer.calls) == 1


def test_quality_evaluator_rejects_empty_audio():
    request = request_for()
    diagnostics = diagnose_wav(
        asyncio.run(DeterministicLocalRenderer().render(request)).audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )

    with pytest.raises(ValueError, match="audio bytes"):
        deterministic_quality_evaluator(request, b"", diagnostics, ())


def test_activity_factory_rejects_wrong_service_and_artifact_store_types(tmp_path):
    renderer = DeterministicLocalRenderer()
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    service = DurableRenderService(artifacts, records)

    with pytest.raises(TypeError, match="service"):
        build_durable_render_activity(object(), renderer, artifacts)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="artifacts"):
        build_durable_render_activity(service, renderer, object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload_mutation",
    [
        lambda payload: payload.clear(),
        lambda payload: payload.update({"episode": {}, "segment": {}}),
        lambda payload: payload.update({"attempt": 0}),
        lambda payload: payload.update({"take": -1}),
        lambda payload: payload.update({"activity_key": ""}),
    ],
)
def test_activity_rejects_malformed_payload_fields(tmp_path, payload_mutation):
    renderer = DeterministicLocalRenderer()
    episode = episode_for()
    payload = payload_for(episode)
    payload_mutation(payload)

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity_for(tmp_path, renderer)(payload))

    assert error.value.type == "WorkflowContractError"
    assert error.value.non_retryable is True
    assert renderer.calls == []


def test_activity_rejects_segment_outside_episode_snapshot(tmp_path):
    renderer = DeterministicLocalRenderer()
    episode = episode_for()
    alternate_request = replace(
        episode.segments[0].render_request,
        segment_id="segment-not-in-episode",
    )
    alternate_segment = replace(
        episode.segments[0],
        segment_id="segment-not-in-episode",
        render_request=alternate_request,
    )
    payload = payload_for(episode)
    payload["segment"] = alternate_segment.to_dict()
    payload["activity_key"] = activity_key_for(
        episode, "render", alternate_segment.segment_id, attempt=1, take=0
    )

    with pytest.raises(ApplicationError, match="activity segment") as error:
        asyncio.run(activity_for(tmp_path, renderer)(payload))

    assert error.value.type == "WorkflowContractError"
    assert renderer.calls == []


def test_activity_maps_render_rejection_to_non_retryable_audio_error(tmp_path):
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    service = DurableRenderService(artifacts, records)

    async def rejected_render(*args, **kwargs):
        del args, kwargs
        raise RenderRejectedError("test renderer rejection")

    service.render_takes = rejected_render  # type: ignore[method-assign]
    activity = build_durable_render_activity(
        service, DeterministicLocalRenderer(), artifacts
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(payload_for(episode_for())))

    assert error.value.type == "MalformedAudioError"
    assert error.value.non_retryable is True


def test_activity_rejects_non_mapping_payload(tmp_path):
    activity = activity_for(tmp_path, DeterministicLocalRenderer())

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity([]))  # type: ignore[arg-type]

    assert error.value.type == "WorkflowContractError"
    assert error.value.non_retryable is True


def test_activity_rejects_multiple_outcomes_from_one_take_service(tmp_path):
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    service = DurableRenderService(artifacts, records)
    original_render_takes = service.render_takes

    async def two_outcomes(request, consent, renderer, *, take_count=1):
        outcomes = await original_render_takes(
            request, consent, renderer, take_count=take_count
        )
        return outcomes + outcomes

    service.render_takes = two_outcomes  # type: ignore[method-assign]
    activity = build_durable_render_activity(
        service, DeterministicLocalRenderer(), artifacts
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(activity(payload_for(episode_for())))

    assert error.value.type == "WorkflowContractError"
    assert error.value.non_retryable is True
