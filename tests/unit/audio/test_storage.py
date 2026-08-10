"""Focused tests for immutable filesystem quality evidence."""

import json
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256

import pytest

from poddown.audio import AudioDiagnostics, CandidateQuality
from poddown.audio.storage import (
    ArtifactIntegrityError,
    FilesystemQualityRecordStore,
    FilesystemTranscriptionRecordStore,
    IdempotencyConflictError,
)
from poddown.domain import FidelityResult, ProviderUsage
from poddown.providers.contracts import TranscriptResult


def quality_fixture() -> CandidateQuality:
    """Return immutable quality with provider transcription provenance."""
    return CandidateQuality(
        candidate_id="candidate-" + "a" * 64,
        fidelity=FidelityResult(True, 1.0, "none"),
        diagnostics=AudioDiagnostics(44_100, 1, 1.0, 0.5, 0.0, 0.1),
        pronunciation_passed=True,
        soft_score=Decimal("0.75"),
        transcription=TranscriptResult(
            text="normalized transcript",
            words=(),
            provider="fake-transcriber",
            model="fake-model-v1",
            usage=ProviderUsage(12, 4),
            request_id="fake-request-1",
            checksum=sha256(b"fixture-audio").hexdigest(),
            cost=Decimal("0.0125"),
            confidence=0.98,
        ),
    )


def test_quality_store_saves_loads_and_replays_immutable_transcription_evidence(
    tmp_path,
):
    store = FilesystemQualityRecordStore(tmp_path)
    quality = quality_fixture()

    store.save(quality)
    first_payload = (tmp_path / "quality" / f"{quality.candidate_id}.json").read_bytes()
    store.save(quality)
    loaded = store.load(quality.candidate_id)

    assert loaded == quality
    assert loaded.transcription == quality.transcription
    assert store.find(quality.candidate_id) == quality
    assert (
        tmp_path / "quality" / f"{quality.candidate_id}.json"
    ).read_bytes() == first_payload


def test_quality_store_rejects_conflicting_evidence(tmp_path):
    store = FilesystemQualityRecordStore(tmp_path)
    quality = quality_fixture()
    store.save(quality)

    with pytest.raises(IdempotencyConflictError):
        store.save(replace(quality, soft_score=Decimal("0.76")))


@pytest.mark.parametrize(
    "record", [b"not-json", json.dumps({"candidate_id": "wrong"}).encode()]
)
def test_quality_store_rejects_malformed_records(tmp_path, record):
    store = FilesystemQualityRecordStore(tmp_path)
    candidate_id = quality_fixture().candidate_id
    path = tmp_path / "quality" / f"{candidate_id}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(record)

    with pytest.raises(ArtifactIntegrityError):
        store.load(candidate_id)


def test_transcription_store_persists_transcript_and_independent_cost_event(
    tmp_path,
):
    """Persist provider response and estimated billing evidence before quality QA."""
    store = FilesystemTranscriptionRecordStore(tmp_path)
    quality = quality_fixture()
    assert quality.transcription is not None

    first = store.save(quality.candidate_id, quality.transcription)
    second = store.save(quality.candidate_id, quality.transcription)

    assert first == second
    assert first.result == quality.transcription
    assert first.cost_event.event_id == (f"transcription-cost-{quality.candidate_id}")
    assert first.cost_event.request_id == quality.transcription.request_id
    assert first.cost_event.audio_checksum == quality.transcription.checksum
    assert (tmp_path / "transcriptions" / f"{quality.candidate_id}.json").exists()


def test_transcription_store_rejects_conflicting_provider_evidence(tmp_path):
    """One candidate cannot acquire a second transcript or second cost event."""
    store = FilesystemTranscriptionRecordStore(tmp_path)
    quality = quality_fixture()
    assert quality.transcription is not None
    store.save(quality.candidate_id, quality.transcription)

    with pytest.raises(IdempotencyConflictError):
        store.save(
            quality.candidate_id,
            replace(quality.transcription, text="different transcript"),
        )
