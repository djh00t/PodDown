"""Unit coverage for provider-evidence creation at the activity boundary."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from poddown.audio import (
    AudioDiagnostics,
    DeterministicLocalRenderer,
    DurableRenderService,
    EpisodeWorkflowInput,
    RenderRequest,
    SegmentWorkflowInput,
    VoiceConsent,
    build_durable_render_activity,
    build_transcription_quality_evaluator,
    diagnose_wav,
)
from poddown.audio.activities import ActivityHandler, QualityEvaluator
from poddown.audio.storage import (
    FilesystemArtifactStore,
    FilesystemRenderRecordStore,
    FilesystemTranscriptionRecordStore,
)
from poddown.domain import ProviderUsage
from poddown.persistence import SQLiteUsageLedger, UsageEvent
from poddown.providers.contracts import ProviderEvidence, TranscriptResult

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
JOB_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b15")
OCCURRED_AT = datetime(2026, 8, 14, 4, 0, tzinfo=UTC)


class FixedTranscriber:
    """Return one normalized transcript without contacting a provider."""

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        self.calls += 1
        return TranscriptResult(
            text="evidence stays normalized",
            words=(),
            provider="fixture-transcriber",
            model="fixture-transcriber-v1",
            usage=ProviderUsage(12, 4),
            request_id="transcribe-request-1",
            checksum=sha256(audio).hexdigest(),
            cost=Decimal("0.0040"),
        )


def _request() -> RenderRequest:
    return RenderRequest(
        episode_id="episode-1",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="evidence stays normalized",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )


def _usage_event(evidence: ProviderEvidence) -> UsageEvent:
    return UsageEvent(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        job_id=JOB_ID,
        provider_request_id=evidence.request_id,
        operation=evidence.operation,
        units=evidence.usage,
        currency=evidence.currency,
        estimated_cost=evidence.estimated_cost,
        reconciled_cost=evidence.reconciled_cost,
        created_at=evidence.occurred_at,
    )


def _record_with_ledger(
    ledger: SQLiteUsageLedger, recorded: list[ProviderEvidence]
) -> Callable[[ProviderEvidence], UsageEvent]:
    def record(evidence: ProviderEvidence) -> UsageEvent:
        recorded.append(evidence)
        return ledger.record_evidence(evidence, _usage_event(evidence))[1]

    return record


async def _evaluate(
    evaluator: QualityEvaluator,
    request: RenderRequest,
    audio: bytes,
    diagnostics: AudioDiagnostics,
    critical_tokens: tuple[str, ...],
) -> object:
    """Await either the async evaluator contract or a synchronous test double."""
    result = evaluator(request, audio, diagnostics, critical_tokens)
    if isinstance(result, Awaitable):
        return await result
    return result


async def _run_activity(
    handler: ActivityHandler, payload: dict[str, object]
) -> dict[str, object]:
    """Await an activity handler with a concrete coroutine return type."""
    return await handler(payload)


def test_transcription_evidence_recorder_is_idempotent_across_replay(
    tmp_path: Path,
) -> None:
    """A replayed transcript must not create a second durable cost event."""
    ledger = SQLiteUsageLedger(tmp_path / "evidence.sqlite3")
    recorded: list[ProviderEvidence] = []
    transcriber = FixedTranscriber()
    evaluator = build_transcription_quality_evaluator(
        transcriber,
        transcription_records=FilesystemTranscriptionRecordStore(
            tmp_path / "transcriptions"
        ),
        provider_evidence_recorder=_record_with_ledger(ledger, recorded),
        provider_evidence_occurred_at=OCCURRED_AT,
    )
    renderer = DeterministicLocalRenderer()
    request = _request()
    rendered = asyncio.run(renderer.render(request))
    diagnostics = diagnose_wav(
        rendered.audio_bytes,
        expected_sample_rate_hz=request.sample_rate_hz,
        expected_channels=1,
    )

    asyncio.run(
        _evaluate(evaluator, request, rendered.audio_bytes, diagnostics, ("evidence",))
    )
    asyncio.run(
        _evaluate(evaluator, request, rendered.audio_bytes, diagnostics, ("evidence",))
    )

    assert transcriber.calls == 1
    assert len(recorded) == 1
    assert recorded[0].evidence_kind == "provider-live"
    assert recorded[0].input_sha256 == sha256(rendered.audio_bytes).hexdigest()
    assert recorded[0].output_sha256 == sha256(b"evidence stays normalized").hexdigest()
    assert ledger.list_for_job(TENANT_ID, JOB_ID) == (_usage_event(recorded[0]),)


def test_render_activity_records_replay_safe_local_evidence(tmp_path: Path) -> None:
    """The durable activity records synthetic evidence before returning output."""
    ledger = SQLiteUsageLedger(tmp_path / "evidence.sqlite3")
    recorded: list[ProviderEvidence] = []
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        DeterministicLocalRenderer(),
        artifacts,
        provider_evidence_recorder=_record_with_ledger(ledger, recorded),
        provider_evidence_occurred_at=OCCURRED_AT,
    )
    request = _request()
    episode = EpisodeWorkflowInput(
        episode_id=request.episode_id,
        episode_version=request.episode_version,
        segments=(
            SegmentWorkflowInput(
                segment_id=request.segment_id,
                render_request=request,
                consent=VoiceConsent(
                    request.voice_asset_id,
                    "consent-1",
                    frozenset({"local"}),
                ),
                critical_tokens=("evidence",),
            ),
        ),
    )
    from poddown.audio.workflow import activity_key_for

    payload = {
        "episode": episode.to_dict(),
        "segment": episode.segments[0].to_dict(),
        "attempt": 1,
        "take": 0,
        "activity_key": activity_key_for(
            episode, "render", request.segment_id, attempt=1, take=0
        ),
    }

    first = asyncio.run(_run_activity(activity, payload))
    second = asyncio.run(_run_activity(activity, payload))

    assert first == second
    assert len(recorded) == 1
    assert recorded[0].evidence_kind == "synthetic"
    assert (
        recorded[0].input_sha256
        == sha256(request.expected_spoken_text.encode("utf-8")).hexdigest()
    )
    assert ledger.list_for_job(TENANT_ID, JOB_ID) == (_usage_event(recorded[0]),)


def test_render_activity_factory_binds_evidence_to_episode_scope(
    tmp_path: Path,
) -> None:
    """Per-episode evidence scopes survive the Temporal activity payload boundary."""
    ledger = SQLiteUsageLedger(tmp_path / "evidence.sqlite3")
    recorded: list[ProviderEvidence] = []
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records", artifacts)
    request = replace(_request(), episode_id=str(JOB_ID))
    episode = EpisodeWorkflowInput(
        episode_id=request.episode_id,
        episode_version=request.episode_version,
        tenant_id=str(TENANT_ID),
        project_id=str(PROJECT_ID),
        segments=(
            SegmentWorkflowInput(
                segment_id=request.segment_id,
                render_request=request,
                consent=VoiceConsent(
                    request.voice_asset_id,
                    "consent-1",
                    frozenset({"local"}),
                ),
                critical_tokens=("evidence",),
            ),
        ),
    )

    def recorder_factory(
        scoped_episode: EpisodeWorkflowInput,
    ) -> Callable[[ProviderEvidence], object]:
        assert scoped_episode.tenant_id == str(TENANT_ID)
        assert scoped_episode.project_id == str(PROJECT_ID)
        return _record_with_ledger(ledger, recorded)

    activity = build_durable_render_activity(
        DurableRenderService(artifacts, records),
        DeterministicLocalRenderer(),
        artifacts,
        provider_evidence_recorder_factory=recorder_factory,
        provider_evidence_occurred_at=OCCURRED_AT,
    )
    from poddown.audio.workflow import activity_key_for

    payload = {
        "episode": episode.to_dict(),
        "segment": episode.segments[0].to_dict(),
        "attempt": 1,
        "take": 0,
        "activity_key": activity_key_for(
            episode, "render", request.segment_id, attempt=1, take=0
        ),
    }

    result = asyncio.run(_run_activity(activity, payload))

    assert result["candidate_id"]
    assert len(recorded) == 1
    assert ledger.list_for_job(TENANT_ID, JOB_ID) == (_usage_event(recorded[0]),)
