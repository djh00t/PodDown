"""Opt-in MinIO integration coverage for the S3-compatible object store."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from pytest_bdd import given, scenarios, then, when

from poddown.object_storage import ObjectIntegrityError, ObjectScopeError
from poddown.s3_boto_transport import Boto3S3Transport
from poddown.s3_object_storage import S3ObjectStore, S3StorageSettings

pytestmark = pytest.mark.minio

scenarios("../features/minio_object_storage.feature")

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
OTHER_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")


@dataclass(frozen=True, slots=True)
class MinioSettings:
    """Secret-free connection settings read only when integration is enabled."""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str


def _settings_or_skip() -> MinioSettings:
    """Require an explicitly enabled, local-only MinIO target."""
    if os.getenv("PODDOWN_MINIO_TESTS") != "1":
        pytest.skip("set PODDOWN_MINIO_TESTS=1 for the local MinIO integration")

    endpoint = os.getenv("PODDOWN_MINIO_ENDPOINT", "http://127.0.0.1:9000").strip()
    parsed = urlparse(endpoint)
    try:
        loopback_host = (
            parsed.hostname is not None and ip_address(parsed.hostname).is_loopback
        )
    except ValueError:
        loopback_host = False
    if parsed.scheme not in {"http", "https"} or not loopback_host:
        pytest.skip("MinIO integration is restricted to a local endpoint")

    access_key = os.getenv("PODDOWN_MINIO_ACCESS_KEY", "").strip()
    secret_key = os.getenv("PODDOWN_MINIO_SECRET_KEY", "").strip()
    if not access_key or not secret_key:
        pytest.skip("MinIO integration requires local-only access-key environment refs")

    bucket = os.getenv("PODDOWN_MINIO_BUCKET", "poddown-integration").strip()
    if not bucket:
        pytest.skip("PODDOWN_MINIO_BUCKET must not be empty")
    return MinioSettings(endpoint, access_key, secret_key, bucket)


@pytest.fixture
def minio_context() -> Iterator[dict[str, Any]]:
    """Create a scoped S3 adapter without contacting MinIO by default."""
    settings = _settings_or_skip()
    client = boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name="us-east-1",
    )
    created_bucket = False
    try:
        client.head_bucket(Bucket=settings.bucket)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        client.create_bucket(Bucket=settings.bucket)
        created_bucket = True

    context: dict[str, Any] = {
        "client": client,
        "bucket": settings.bucket,
        "store": S3ObjectStore(
            Boto3S3Transport(
                S3StorageSettings(
                    endpoint=settings.endpoint,
                    bucket=settings.bucket,
                    secret_ref="secret://poddown-minio-integration",
                    allow_insecure_local=True,
                ),
                lambda _secret_ref: (settings.access_key, settings.secret_key),
            ),
            endpoint=settings.endpoint,
            bucket=settings.bucket,
            secret_ref="secret://poddown-minio-integration",
            allow_insecure_local=True,
        ),
        "data": f"# MinIO fixture {uuid4()}\n".encode(),
    }
    try:
        yield context
    finally:
        reference = context.get("reference")
        if reference is not None:
            client.delete_object(Bucket=settings.bucket, Key=reference.storage_key)
        if created_bucket:
            client.delete_bucket(Bucket=settings.bucket)


def _store(context: dict[str, Any]) -> S3ObjectStore:
    store = context["store"]
    assert isinstance(store, S3ObjectStore)
    return store


def test_minio_round_trip_and_idempotent_replay(
    minio_context: dict[str, Any],
) -> None:
    store = _store(minio_context)
    data = minio_context["data"]
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="minio.md",
        media_type="text/markdown",
        data=data,
    )
    minio_context["reference"] = reference

    replay = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="minio.md",
        media_type="text/markdown",
        data=data,
    )

    assert replay == reference
    assert store.read(TENANT_ID, PROJECT_ID, reference) == data


def test_minio_rejects_cross_project_reads_and_corrupt_bytes(
    minio_context: dict[str, Any],
) -> None:
    store = _store(minio_context)
    client = minio_context["client"]
    bucket = minio_context["bucket"]
    data = minio_context["data"]
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="minio.md",
        media_type="text/markdown",
        data=data,
    )
    minio_context["reference"] = reference

    with pytest.raises(ObjectScopeError):
        store.read(TENANT_ID, OTHER_PROJECT_ID, reference)

    with pytest.raises(ObjectScopeError):
        store.read(
            UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
            PROJECT_ID,
            reference,
        )

    client.put_object(
        Bucket=bucket,
        Key=reference.storage_key,
        Body=b"x" * len(data),
        ContentType=reference.media_type,
        Metadata={
            "poddown-sha256": reference.sha256,
            "poddown-byte-count": str(reference.byte_count),
            "poddown-media-type": reference.media_type,
            "poddown-name": reference.name,
            "poddown-tenant-id": str(reference.tenant_id),
            "poddown-project-id": str(reference.project_id),
        },
    )
    with pytest.raises(ObjectIntegrityError):
        store.read(TENANT_ID, PROJECT_ID, reference)


@given("a local MinIO object store")
def local_minio_store(minio_context: dict[str, Any]) -> None:
    assert isinstance(minio_context["store"], S3ObjectStore)


@when("I store and replay a tenant-scoped Markdown object")
def store_and_replay_minio_object(minio_context: dict[str, Any]) -> None:
    store = _store(minio_context)
    data = minio_context["data"]
    reference = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="bdd-minio.md",
        media_type="text/markdown",
        data=data,
    )
    minio_context["reference"] = reference
    minio_context["replay"] = store.put(
        TENANT_ID,
        PROJECT_ID,
        name="bdd-minio.md",
        media_type="text/markdown",
        data=data,
    )


@then("the local MinIO bytes are exact and replay is idempotent")
def verify_minio_replay(minio_context: dict[str, Any]) -> None:
    store = _store(minio_context)
    reference = minio_context["reference"]
    assert minio_context["replay"] == reference
    assert store.read(TENANT_ID, PROJECT_ID, reference) == minio_context["data"]
