"""BDD bindings for the concrete Boto3-compatible S3 transport."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.object_storage import ObjectNotFound
from poddown.s3_boto_transport import Boto3S3Transport
from poddown.s3_object_storage import S3StorageSettings

scenarios("../features/s3_boto_transport.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
DATA = b"boto3 transport bytes"


class _Missing(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "NoSuchKey"}}


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def put_object(self, **kwargs: object) -> None:
        self.calls.append(("put_object", kwargs))
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        body = kwargs["Body"]
        metadata = kwargs["Metadata"]
        assert isinstance(bucket, str)
        assert isinstance(key, str)
        assert isinstance(body, bytes)
        assert isinstance(metadata, dict)
        self.objects[(bucket, key)] = (body, metadata)

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_object", kwargs))
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        assert isinstance(bucket, str)
        assert isinstance(key, str)
        stored = self.objects.get((bucket, key))
        if stored is None:
            raise _Missing()
        data, metadata = stored
        return {"Body": _Body(data), "Metadata": metadata}

    def delete_object(self, **kwargs: object) -> None:
        self.calls.append(("delete_object", kwargs))
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        assert isinstance(bucket, str)
        assert isinstance(key, str)
        if (bucket, key) not in self.objects:
            raise _Missing()
        del self.objects[(bucket, key)]

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list_objects_v2", kwargs))
        bucket = kwargs["Bucket"]
        prefix = kwargs["Prefix"]
        token = kwargs.get("ContinuationToken")
        assert isinstance(bucket, str)
        assert isinstance(prefix, str)
        assert token is None or isinstance(token, str)
        keys = sorted(
            key for (stored_bucket, key) in self.objects if stored_bucket == bucket
        )
        matching = [key for key in keys if key.startswith(prefix)]
        if token is None:
            page = matching[:1]
            truncated = len(matching) > 1
        else:
            page = matching[1:]
            truncated = False
        return {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": truncated,
            "NextContinuationToken": "page-2" if truncated else None,
        }


@given("a fake Boto3 client and secret resolver")
def fake_boto_client(context: Any) -> None:
    client = _Client()
    settings = S3StorageSettings(
        endpoint="http://minio.local:9000",
        bucket="poddown",
        secret_ref="secret://minio/poddown",
        allow_insecure_local=True,
    )

    def factory(service: str, **kwargs: object) -> _Client:
        assert service == "s3"
        context.values["client_kwargs"] = kwargs
        return client

    def resolve(secret_ref: str) -> tuple[str, str]:
        assert secret_ref == settings.secret_ref
        return ("local-user", "local-password")

    context.values["client"] = client
    context.values["transport"] = Boto3S3Transport(
        settings,
        resolve,
        client_factory=factory,
    )
    context.values["reference"] = (
        f"tenants/{TENANT}/projects/{PROJECT}/objects/"
        f"{sha256(DATA).hexdigest()[:2]}/{sha256(DATA).hexdigest()}"
    )


@when("I put and read an object through the Boto3 transport")
def put_read_boto(context: Any) -> None:
    transport = context.values["transport"]
    key = context.values["reference"]
    transport.put_object(
        key,
        DATA,
        {"poddown-sha256": sha256(DATA).hexdigest()},
    )
    context.values["stored"] = transport.get_object(key)


@then("the transport uses the configured bucket and secret reference")
def boto_configuration(context: Any) -> None:
    assert context.values["stored"].data == DATA
    assert context.values["client_kwargs"]["endpoint_url"] == (
        "http://minio.local:9000"
    )
    assert context.values["transport"].settings.secret_ref == "secret://minio/poddown"
    assert (
        "local-password"
        not in context.values["transport"].settings.to_record().__repr__()
    )


@when("I read a missing object through the Boto3 transport")
def read_missing_boto(context: Any) -> None:
    with pytest.raises(ObjectNotFound, match="missing"):
        context.values["transport"].get_object("missing")


@then("the missing object is reported without provider details")
def missing_boto_safe(context: Any) -> None:
    assert all(name in {"get_object"} for name, _ in context.values["client"].calls)


@when("I list objects through the Boto3 transport")
def list_boto_objects(context: Any) -> None:
    transport = context.values["transport"]
    prefix = f"tenants/{TENANT}/projects/{PROJECT}/objects/"
    transport.put_object(f"{prefix}aa/a-key", DATA, {})
    transport.put_object(f"{prefix}bb/b-key", DATA, {})
    transport.put_object("tenants/other/object", DATA, {})
    context.values["listed"] = transport.list_objects(prefix)


@then("all matching keys are returned in provider order")
def boto_inventory_is_complete(context: Any) -> None:
    assert context.values["listed"] == (
        f"tenants/{TENANT}/projects/{PROJECT}/objects/aa/a-key",
        f"tenants/{TENANT}/projects/{PROJECT}/objects/bb/b-key",
    )
