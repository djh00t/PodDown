"""Deterministic test doubles for external transport boundaries."""

from __future__ import annotations

from io import BytesIO
from typing import Any


class _NotFound(Exception):
    response = {"Error": {"Code": "404"}}


class _PreconditionFailed(Exception):
    response = {"Error": {"Code": "PreconditionFailed"}}


class FakeS3Client:
    """In-memory fake for the S3 calls made by ``S3ObjectStore``."""

    def __init__(self, *, versioned: bool = False) -> None:
        self.versioned = versioned
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self._versions: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._version_number = 0
        self.head_requests: list[dict[str, str]] = []
        self.get_requests: list[dict[str, str]] = []
        self.race_winner_body: bytes | None = None
        self.race_triggered = False
        self.overwrite_after_head_metadata: dict[str, str] | None = None

    def head_object(
        self,
        *,
        Bucket: str,
        Key: str,
        ChecksumMode: str,
    ) -> dict[str, Any]:
        if ChecksumMode != "ENABLED":
            raise AssertionError("S3 HEAD must enable checksum response")
        self.head_requests.append(
            {"Bucket": Bucket, "Key": Key, "ChecksumMode": ChecksumMode}
        )
        try:
            object_data = self.objects[(Bucket, Key)]
        except KeyError as error:
            raise _NotFound from error
        response = {**object_data, "Metadata": dict(object_data["Metadata"])}
        if self.overwrite_after_head_metadata is not None:
            overwritten = {
                **object_data,
                "Metadata": dict(self.overwrite_after_head_metadata),
            }
            self._save_object(Bucket, Key, overwritten)
            self.overwrite_after_head_metadata = None
        return response

    def put_object(self, **kwargs: Any) -> None:
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        if self.race_winner_body is not None and not self.race_triggered:
            metadata = dict(kwargs["Metadata"])
            self._save_object(
                bucket,
                key,
                {
                    "ContentLength": int(metadata["byte-count"]),
                    "Metadata": metadata,
                    "ChecksumSHA256": kwargs["ChecksumSHA256"],
                    "Body": self.race_winner_body,
                },
            )
            self.race_triggered = True
            raise _PreconditionFailed
        if kwargs.get("IfNoneMatch") == "*" and (bucket, key) in self.objects:
            raise _PreconditionFailed
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        self._save_object(
            bucket,
            key,
            {
                "ContentLength": len(body),
                "Metadata": kwargs["Metadata"],
                "ChecksumSHA256": kwargs["ChecksumSHA256"],
                "Body": body,
            },
        )

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        ChecksumMode: str,
        VersionId: str | None = None,
    ) -> dict[str, Any]:
        if ChecksumMode != "ENABLED":
            raise AssertionError("S3 GET must enable checksum response")
        request = {"Bucket": Bucket, "Key": Key, "ChecksumMode": ChecksumMode}
        if VersionId is not None:
            request["VersionId"] = VersionId
        self.get_requests.append(request)
        try:
            if VersionId is None:
                object_data = self.objects[(Bucket, Key)]
            else:
                object_data = self._versions[(Bucket, Key)][VersionId]
        except KeyError as error:
            raise _NotFound from error
        return {
            **object_data,
            "Metadata": dict(object_data["Metadata"]),
            "Body": BytesIO(object_data["Body"]),
        }

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, **kwargs: Any) -> dict[str, object]:
        bucket = kwargs["Bucket"]
        prefix = kwargs["Prefix"]
        return {
            "Contents": [
                {"Key": key}
                for stored_bucket, key in sorted(self.objects)
                if stored_bucket == bucket and key.startswith(prefix)
            ],
            "IsTruncated": False,
        }

    def _save_object(
        self,
        bucket: str,
        key: str,
        object_data: dict[str, Any],
    ) -> None:
        if self.versioned:
            self._version_number += 1
            version_id = f"version-{self._version_number}"
            object_data = {**object_data, "VersionId": version_id}
            self._versions.setdefault((bucket, key), {})[version_id] = object_data
        self.objects[(bucket, key)] = object_data
