"""Integration tests for S3-backed immutable publication with a fake client."""

from __future__ import annotations

import base64
from uuid import UUID

import pytest

from poddown.object_storage import (
    ObjectIntegrityError,
    ObjectScopeError,
    S3ObjectStore,
)
from tests.fakes import FakeS3Client

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")


def test_s3_store_replays_only_exact_scoped_bytes_and_metadata() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(bucket="poddown-test", client=client)

    first = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=b"verified episode bytes",
    )
    second = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=b"verified episode bytes",
    )

    assert second == first
    assert store.read(TENANT, PROJECT, first) == b"verified episode bytes"
    stored = client.objects[("poddown-test", first.storage_key)]
    assert stored["ChecksumSHA256"] == base64.b64encode(
        bytes.fromhex(first.sha256)
    ).decode("ascii")
    assert stored["Metadata"] == {
        "schema": "poddown.object",
        "schema-version": "1.0",
        "tenant-id": str(TENANT),
        "project-id": str(PROJECT),
        "name": "episode.mp3",
        "media-type": "audio/mpeg",
        "byte-count": str(len(b"verified episode bytes")),
        "sha256": first.sha256,
        "storage-key": first.storage_key,
    }

    stored["Metadata"] = {"sha256": first.sha256}
    with pytest.raises(ObjectIntegrityError, match="metadata"):
        store.read(TENANT, PROJECT, first)


def test_s3_head_requests_checksum_mode_before_verifying_checksum() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(bucket="poddown-test", client=client)

    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=b"verified episode bytes",
    )

    assert client.head_requests
    assert client.head_requests[-1] == {
        "Bucket": "poddown-test",
        "Key": reference.storage_key,
        "ChecksumMode": "ENABLED",
    }


def test_s3_identical_put_rejects_corrupted_existing_bytes_with_stale_head() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(bucket="poddown-test", client=client)
    data = b"verified episode bytes"
    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=data,
    )
    client.objects[("poddown-test", reference.storage_key)]["Body"] = b"!" * len(data)

    with pytest.raises(ObjectIntegrityError, match="bytes"):
        store.put(
            TENANT,
            PROJECT,
            name="episode.mp3",
            media_type="audio/mpeg",
            data=data,
        )


def test_s3_precondition_race_rejects_corrupted_winner_bytes() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(bucket="poddown-test", client=client)
    data = b"verified episode bytes"
    client.race_winner_body = b"!" * len(data)

    with pytest.raises(ObjectIntegrityError, match="bytes"):
        store.put(
            TENANT,
            PROJECT,
            name="episode.mp3",
            media_type="audio/mpeg",
            data=data,
        )

    assert client.race_triggered is True


def test_s3_schema_metadata_is_frozen_for_replay_and_scope() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(bucket="poddown-test", client=client)
    data = b"verified episode bytes"
    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=data,
    )
    client.objects[("poddown-test", reference.storage_key)]["Metadata"]["schema"] = (
        "other.object"
    )

    with pytest.raises(ObjectIntegrityError, match="metadata"):
        store.put(
            TENANT,
            PROJECT,
            name="episode.mp3",
            media_type="audio/mpeg",
            data=data,
        )

    with pytest.raises(ObjectIntegrityError, match="metadata"):
        store.read(TENANT, PROJECT, reference)

    with pytest.raises(ObjectScopeError):
        store.read(
            UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
            PROJECT,
            reference,
        )


def test_s3_store_rejects_existing_object_with_mismatched_metadata() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(bucket="poddown-test", client=client)
    reference = store.put(
        TENANT,
        PROJECT,
        name="episode.mp3",
        media_type="audio/mpeg",
        data=b"verified episode bytes",
    )
    client.objects[("poddown-test", reference.storage_key)]["Metadata"] = {
        "tenant-id": str(TENANT),
        "project-id": str(PROJECT),
        "name": "different.mp3",
        "media-type": "audio/mpeg",
        "byte-count": str(reference.byte_count),
        "sha256": reference.sha256,
        "storage-key": reference.storage_key,
    }

    with pytest.raises(ObjectIntegrityError, match="metadata"):
        store.put(
            TENANT,
            PROJECT,
            name="episode.mp3",
            media_type="audio/mpeg",
            data=b"verified episode bytes",
        )
