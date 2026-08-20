"""Unit contracts for S3-compatible object-storage configuration."""

from __future__ import annotations

import pytest

from poddown.s3_object_storage import S3StorageSettings


def test_remote_s3_requires_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        S3StorageSettings(
            endpoint="http://s3.example.com",
            bucket="poddown",
            secret_ref="secret://s3/poddown",
        )


def test_local_minio_can_explicitly_use_http() -> None:
    settings = S3StorageSettings(
        endpoint="http://minio.local:9000",
        bucket="poddown",
        secret_ref="secret://minio/poddown",
        allow_insecure_local=True,
    )
    assert settings.to_record()["secret_ref"] == "secret://minio/poddown"
