"""Unit coverage for content-addressed immutable package artifacts."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest
from poddown.artifacts import (
    ArtifactRef,
    FilesystemArtifactStore,
)


def test_store_returns_an_immutable_reference_for_exact_content(tmp_path):
    """Catch artifact references that do not bind metadata to exact bytes."""
    content = b"verified episode WAV bytes"

    reference = FilesystemArtifactStore(tmp_path).put(
        "episode.wav", "audio/wav", content
    )

    assert reference == ArtifactRef(
        name="episode.wav",
        media_type="audio/wav",
        byte_count=26,
        sha256="376efc7b94637903b85b805018bbe33ba67cd743a3860050ebd9419eb73febfa",
        storage_key=reference.storage_key,
    )
    assert (tmp_path / reference.storage_key).read_bytes() == content
    with pytest.raises(FrozenInstanceError):
        reference.name = "replacement.wav"  # type: ignore[misc]


def test_store_reuses_one_content_addressed_object_for_identical_writes(tmp_path):
    """Catch repeated content writes that create mutable or divergent objects."""
    store = FilesystemArtifactStore(tmp_path)
    content = b"same immutable content"

    first = store.put("episode.wav", "audio/wav", content)
    second = store.put("episode.wav", "audio/wav", content)

    assert first == second
    assert first.sha256 == sha256(content).hexdigest()
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [
        tmp_path / first.storage_key
    ]


def test_store_rejects_missing_or_corrupted_content_at_read_time(tmp_path):
    """Catch reads that trust missing or tampered content-addressed bytes."""
    store = FilesystemArtifactStore(tmp_path)
    reference = store.put("episode.wav", "audio/wav", b"original bytes")
    path = tmp_path / reference.storage_key

    path.unlink()
    with pytest.raises(RuntimeError):
        store.read(reference)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"tampered bytes")
    with pytest.raises(RuntimeError):
        store.read(reference)


@pytest.mark.parametrize(
    "name",
    ["", "../episode.wav", "nested/episode.wav", "/episode.wav", "episode\\.wav"],
)
def test_store_rejects_unsafe_or_noncanonical_artifact_names(tmp_path, name):
    """Catch names that could escape or make package members ambiguous."""
    with pytest.raises(ValueError):
        FilesystemArtifactStore(tmp_path).put(name, "audio/wav", b"bytes")


def test_store_rejects_a_reference_with_mismatched_storage_key(tmp_path):
    """Catch reads that accept a forged path instead of immutable identity."""
    store = FilesystemArtifactStore(tmp_path)
    reference = store.put("episode.wav", "audio/wav", b"original bytes")

    with pytest.raises(RuntimeError):
        store.read(replace(reference, storage_key="../../forged"))
