"""Concrete Boto3 transport for the validated S3 object-store port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Protocol, cast

from poddown.object_storage import ObjectNotFound
from poddown.s3_object_storage import (
    S3StorageError,
    S3StorageSettings,
    S3StoredObject,
    S3Transport,
)


class Boto3Client(Protocol):
    """Minimal Boto3 client surface required by the object-store port."""

    def put_object(self, **kwargs: object) -> object:
        """Write one object."""

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        """Read one object and its metadata."""

    def delete_object(self, **kwargs: object) -> object:
        """Delete one object."""

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        """List one page of object keys."""


CredentialResolver = Callable[[str], tuple[str, str]]
ClientFactory = Callable[..., Boto3Client]


def _default_client_factory(service_name: str, **kwargs: object) -> Boto3Client:
    """Create a Boto3 client lazily at the external transport boundary."""
    module = import_module("boto3")
    client = getattr(module, "client", None)
    if not callable(client):
        raise S3StorageError("Boto3 client factory is unavailable")
    return cast(Boto3Client, client(service_name, **kwargs))


def _provider_error_code(error: BaseException) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return None
    code = details.get("Code")
    return code if isinstance(code, str) else None


def _is_missing(error: BaseException) -> bool:
    return _provider_error_code(error) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }


def _safe_metadata(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise S3StorageError("S3 object metadata is invalid")
    return dict(value)


class Boto3S3Transport(S3Transport):
    """Use Boto3 without allowing credentials into PodDown records."""

    def __init__(
        self,
        settings: S3StorageSettings,
        credential_resolver: CredentialResolver,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not isinstance(settings, S3StorageSettings):
            raise TypeError("settings must be S3StorageSettings")
        if not callable(credential_resolver):
            raise TypeError("credential_resolver must be callable")
        access_key, secret_key = credential_resolver(settings.secret_ref)
        if (
            not isinstance(access_key, str)
            or not access_key
            or not isinstance(secret_key, str)
            or not secret_key
        ):
            raise S3StorageError("S3 credential resolver returned invalid credentials")
        self.settings = settings
        factory = client_factory or _default_client_factory
        self._client = factory(
            "s3",
            endpoint_url=settings.endpoint,
            region_name=settings.region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put_object(
        self,
        key: str,
        data: bytes,
        metadata: dict[str, str],
    ) -> None:
        """Write bytes and immutable metadata to the configured bucket."""
        if not isinstance(key, str) or not key:
            raise S3StorageError("S3 object key is invalid")
        if type(data) is not bytes:
            raise S3StorageError("S3 object data must be bytes")
        safe_metadata = _safe_metadata(metadata)
        request: dict[str, object] = {
            "Bucket": self.settings.bucket,
            "Key": key,
            "Body": data,
            "Metadata": safe_metadata,
        }
        media_type = safe_metadata.get("poddown-media-type")
        if media_type is not None:
            request["ContentType"] = media_type
        try:
            self._client.put_object(**request)
        except Exception as error:
            raise S3StorageError("S3 object write failed") from error

    def get_object(self, key: str) -> S3StoredObject:
        """Read bytes and metadata, normalizing provider errors safely."""
        if not isinstance(key, str) or not key:
            raise S3StorageError("S3 object key is invalid")
        try:
            response = self._client.get_object(
                Bucket=self.settings.bucket,
                Key=key,
            )
        except Exception as error:
            if _is_missing(error):
                raise ObjectNotFound("S3 object is missing") from error
            raise S3StorageError("S3 object read failed") from error
        body = response.get("Body")
        read = getattr(body, "read", None)
        if not callable(read):
            raise S3StorageError("S3 response body is invalid")
        try:
            data = read()
        except Exception as error:
            raise S3StorageError("S3 object body read failed") from error
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if type(data) is not bytes:
            raise S3StorageError("S3 object body is invalid")
        return S3StoredObject(
            data=data,
            metadata=_safe_metadata(response.get("Metadata", {})),
        )

    def delete_object(self, key: str) -> None:
        """Delete one exact object key and normalize missing-object errors."""
        if not isinstance(key, str) or not key:
            raise S3StorageError("S3 object key is invalid")
        try:
            self._client.delete_object(Bucket=self.settings.bucket, Key=key)
        except Exception as error:
            if _is_missing(error):
                raise ObjectNotFound("S3 object is missing") from error
            raise S3StorageError("S3 object deletion failed") from error

    def list_objects(self, prefix: str) -> tuple[str, ...]:
        """List all keys under a prefix, following provider pagination."""
        if not isinstance(prefix, str) or not prefix:
            raise S3StorageError("S3 object prefix is invalid")
        keys: list[str] = []
        continuation: str | None = None
        while True:
            request: dict[str, object] = {
                "Bucket": self.settings.bucket,
                "Prefix": prefix,
            }
            if continuation is not None:
                request["ContinuationToken"] = continuation
            try:
                response = self._client.list_objects_v2(**request)
            except Exception as error:
                raise S3StorageError("S3 object inventory failed") from error
            if not isinstance(response, Mapping):
                raise S3StorageError("S3 object inventory response is invalid")
            contents = response.get("Contents", [])
            if not isinstance(contents, (list, tuple)):
                raise S3StorageError("S3 object inventory contents are invalid")
            for entry in contents:
                if not isinstance(entry, Mapping) or not isinstance(
                    entry.get("Key"), str
                ):
                    raise S3StorageError("S3 object inventory key is invalid")
                keys.append(entry["Key"])
            truncated = response.get("IsTruncated", False)
            if not isinstance(truncated, bool):
                raise S3StorageError("S3 object inventory pagination is invalid")
            if not truncated:
                return tuple(keys)
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise S3StorageError("S3 object inventory continuation is invalid")
            if next_token == continuation:
                raise S3StorageError("S3 object inventory pagination repeated")
            continuation = next_token


__all__ = ["Boto3Client", "Boto3S3Transport", "ClientFactory", "CredentialResolver"]
