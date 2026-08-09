"""End-to-end persistence checks for deterministic durable audio rendering."""

import asyncio
from dataclasses import replace

from poddown.audio.contracts import RenderRequest
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore


def _request(*, attempt: int = 1) -> RenderRequest:
    """Build a rights-cleared request for the deterministic local fixture."""
    return RenderRequest(
        episode_id="durable-integration-episode",
        episode_version="v1",
        segment_id="segment-001",
        speaker_id="host",
        expected_spoken_text="Persistence must survive a fresh service instance.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
        attempt=attempt,
    )


def _consent() -> VoiceConsent:
    """Return rights evidence matching the local fixture request."""
    return VoiceConsent(
        "voice-host-v1", "consent-integration-001", frozenset({"local"})
    )


def _render(
    service: DurableRenderService,
    request: RenderRequest,
    renderer: DeterministicLocalRenderer,
    *,
    take_count: int,
):
    """Run the asynchronous service through its public render boundary."""
    return asyncio.run(
        service.render_takes(request, _consent(), renderer, take_count=take_count)
    )


def test_filesystem_records_replay_across_fresh_service_instances_and_attempts(
    tmp_path,
):
    """A restart must replay exact evidence, while a new attempt renders anew."""
    artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    records = FilesystemRenderRecordStore(tmp_path / "records")
    request = _request()
    initial_renderer = DeterministicLocalRenderer()
    initial_service = DurableRenderService(artifacts, records)

    initial = _render(initial_service, request, initial_renderer, take_count=3)
    assert len(initial) == 3
    assert [outcome.candidate.take_index for outcome in initial] == [0, 1, 2]
    assert all(outcome.cost_event is not None for outcome in initial)
    initial_artifacts = [
        artifacts.read(outcome.candidate.artifact) for outcome in initial
    ]
    initial_records = [
        records.find(outcome.candidate.idempotency_key) for outcome in initial
    ]
    initial_cost_events = [
        record.cost_event for record in initial_records if record is not None
    ]
    assert len(initial_records) == 3
    assert len(initial_cost_events) == 3
    assert all(record is not None for record in initial_records)
    for outcome, record in zip(initial, initial_records, strict=True):
        assert record is not None
        canonical_request = replace(request, take_index=outcome.candidate.take_index)
        assert record.candidate.attempt == 1
        assert record.candidate.candidate_id == canonical_request.candidate_id
        assert record.candidate.idempotency_key == canonical_request.idempotency_key
        assert record.cost_event is not None

    restarted_artifacts = FilesystemArtifactStore(tmp_path / "artifacts")
    restarted_records = FilesystemRenderRecordStore(tmp_path / "records")
    restarted_renderer = DeterministicLocalRenderer()
    restarted_service = DurableRenderService(restarted_artifacts, restarted_records)

    replayed = _render(restarted_service, request, restarted_renderer, take_count=3)

    assert len(initial_renderer.calls) == 3
    assert len(restarted_renderer.calls) == 0
    assert [outcome.candidate for outcome in replayed] == [
        outcome.candidate for outcome in initial
    ]
    assert [outcome.candidate.artifact.sha256 for outcome in replayed] == [
        outcome.candidate.artifact.sha256 for outcome in initial
    ]
    assert [
        restarted_records.find(outcome.candidate.idempotency_key).cost_event
        for outcome in replayed
    ] == initial_cost_events
    assert all(outcome.cost_event is None for outcome in replayed)
    assert [
        restarted_artifacts.read(outcome.candidate.artifact) for outcome in replayed
    ] == initial_artifacts
    assert all(outcome.replayed for outcome in replayed)

    attempt_two_request = replace(request, attempt=2)
    next_attempt = _render(
        restarted_service,
        attempt_two_request,
        restarted_renderer,
        take_count=1,
    )
    assert len(next_attempt) == 1
    assert next_attempt[0].cost_event is not None
    next_record = restarted_records.find(next_attempt[0].candidate.idempotency_key)
    assert next_record is not None
    assert next_record.candidate.attempt == 2
    assert next_record.candidate.candidate_id == attempt_two_request.candidate_id
    assert next_record.candidate.idempotency_key == attempt_two_request.idempotency_key
    assert next_record.cost_event is not None
    assert next_record.cost_event == next_attempt[0].cost_event

    assert next_attempt[0].candidate.candidate_id != initial[0].candidate.candidate_id
    assert (
        next_attempt[0].candidate.artifact.sha256
        != initial[0].candidate.artifact.sha256
    )
    assert len(restarted_renderer.calls) == 1
