"""Tests for immutable filesystem audio artifacts and render records."""

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256

import pytest

from poddown.audio.contracts import (
    ArtifactRef,
    ProviderCostEvent,
    RenderCandidate,
    RenderOutcome,
)
from poddown.audio.storage import (
    ArtifactIntegrityError,
    FilesystemArtifactStore,
    FilesystemRenderRecordStore,
    IdempotencyConflictError,
)
from poddown.domain import ProviderUsage


def _outcome(artifact: ArtifactRef, **overrides: object) -> RenderOutcome:
    candidate_values: dict[str, object] = {
        "candidate_id": "candidate-001",
        "idempotency_key": "render-001",
        "segment_id": "segment-001",
        "speaker_id": "host",
        "attempt": 1,
        "take_index": 0,
        "voice_asset_id": "voice-host-v1",
        "expected_spoken_text": "The rate is 13.9 hertz.",
        "provider": "local",
        "model": "local-deterministic-v1",
        "request_id": "local-request-001",
        "usage": ProviderUsage(input_units=26, output_units=11),
        "cost": Decimal("0.03125"),
        "artifact": artifact,
    }
    candidate_values.update(overrides)
    candidate = RenderCandidate(**candidate_values)  # type: ignore[arg-type]
    return RenderOutcome(
        candidate=candidate,
        cost_event=ProviderCostEvent(
            event_id="cost-001",
            candidate_id=candidate.candidate_id,
            provider="local",
            usage=ProviderUsage(input_units=26, output_units=11),
            cost=Decimal("0.03125"),
        ),
        replayed=False,
    )


def test_put_returns_a_content_addressed_artifact_and_read_returns_its_bytes(tmp_path):
    """Changing artifact bytes must change their digest-addressed reference."""
    store = FilesystemArtifactStore(tmp_path)

    artifact = store.put(b"audio", media_type="audio/wav")

    assert artifact.sha256 == sha256(b"audio").hexdigest()
    assert artifact.size_bytes == 5
    assert (tmp_path / artifact.relative_path).is_relative_to(tmp_path)
    assert store.read(artifact) == b"audio"


def test_put_of_identical_bytes_reuses_the_existing_immutable_object(tmp_path):
    """Replacing an existing content address with different bytes is a bug."""
    store = FilesystemArtifactStore(tmp_path)

    first = store.put(b"audio", media_type="audio/wav")
    second = store.put(b"audio", media_type="audio/wav")

    assert second == first
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [
        tmp_path / first.relative_path
    ]


@pytest.mark.parametrize(
    "artifact",
    [
        ArtifactRef("a" * 64, "audio/wav", 5, "artifacts/aa/missing.wav"),
        ArtifactRef("a" * 64, "audio/wav", 5, "../escaped.wav"),
        ArtifactRef("a" * 64, "audio/wav", 5, "artifacts/aa/object.wav"),
        ArtifactRef("b" * 64, "audio/wav", 4, "artifacts/bb/object.wav"),
    ],
)
def test_read_rejects_missing_escaping_and_mismatched_references(tmp_path, artifact):
    """Skipping reference validation would permit corrupted or escaped reads."""
    store = FilesystemArtifactStore(tmp_path)
    mismatched = tmp_path / "artifacts/aa/object.wav"
    mismatched.parent.mkdir(parents=True)
    mismatched.write_bytes(b"audio")
    wrong_size = tmp_path / "artifacts/bb/object.wav"
    wrong_size.parent.mkdir(parents=True)
    wrong_size.write_bytes(b"audio")

    with pytest.raises(ArtifactIntegrityError):
        store.read(artifact)


def test_put_refuses_to_overwrite_a_corrupted_existing_object(tmp_path):
    """Repairing a corrupt object during put would conceal storage corruption."""
    store = FilesystemArtifactStore(tmp_path)
    artifact = store.put(b"audio", media_type="audio/wav")
    artifact_path = tmp_path / artifact.relative_path
    artifact_path.write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError):
        store.put(b"audio", media_type="audio/wav")

    assert artifact_path.read_bytes() == b"tampered"


def test_save_and_load_round_trip_all_render_metadata_and_decimal_costs(tmp_path):
    """Dropping nested render metadata would make replay and billing unverifiable."""
    artifacts = FilesystemArtifactStore(tmp_path)
    outcome = _outcome(artifacts.put(b"audio", media_type="audio/wav"))
    FilesystemRenderRecordStore(tmp_path).save(outcome)

    loaded = FilesystemRenderRecordStore(tmp_path).load("render-001")

    assert loaded == outcome


def test_save_of_an_identical_outcome_is_idempotent(tmp_path):
    """A retry must not produce a second record for one idempotency key."""
    artifacts = FilesystemArtifactStore(tmp_path)
    outcome = _outcome(artifacts.put(b"audio", media_type="audio/wav"))
    records = FilesystemRenderRecordStore(tmp_path)

    records.save(outcome)
    records.save(outcome)

    assert records.load("render-001") == outcome


def test_save_rejects_a_different_outcome_for_an_existing_idempotency_key(tmp_path):
    """Accepting conflicting replay evidence would break exactly-once rendering."""
    artifacts = FilesystemArtifactStore(tmp_path)
    outcome = _outcome(artifacts.put(b"audio", media_type="audio/wav"))
    conflicting = replace(
        outcome,
        candidate=replace(outcome.candidate, request_id="different-request"),
    )
    records = FilesystemRenderRecordStore(tmp_path)
    records.save(outcome)

    with pytest.raises(IdempotencyConflictError):
        records.save(conflicting)


def test_load_rejects_missing_and_malformed_json_records(tmp_path):
    """Treating absent or malformed replay evidence as valid would hide data loss."""
    records = FilesystemRenderRecordStore(tmp_path)

    with pytest.raises(ArtifactIntegrityError):
        records.load("missing")

    record_path = tmp_path / "records" / "render-001.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text("not json", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        records.load("render-001")


def test_find_returns_none_for_an_unknown_idempotency_key(tmp_path):
    """A first render must distinguish no record from a corrupted record."""
    assert FilesystemRenderRecordStore(tmp_path).find("unknown") is None
