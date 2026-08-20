"""Unit coverage for optional provider, database, and durable-object ports."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

import poddown.nats_runtime as nats_runtime
import poddown.postgres_runtime as postgres_runtime
from poddown.durable_s3_object_storage import DurableS3ObjectStore
from poddown.nats_outbox import NatsOutboxError
from poddown.object_storage import (
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectRef,
    ObjectStorageError,
    ObjectStore,
    storage_key_for,
)
from poddown.postgres_objects import ObjectReferenceNotFound

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
DATA = b"durable boundary bytes"


class _JetStream:
    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        return (subject, payload, headers)


class _NatsConnection:
    def __init__(self) -> None:
        self.jetstream_client = _JetStream()
        self.closed = False

    def jetstream(self) -> _JetStream:
        return self.jetstream_client

    async def close(self) -> None:
        self.closed = True


def test_nats_settings_and_client_fail_closed() -> None:
    for address in ("", "http://localhost:4222", "nats://user:password@localhost"):
        with pytest.raises(ValueError):
            nats_runtime.NatsRuntimeSettings(address=address)
    with pytest.raises(ValueError, match="client_name"):
        nats_runtime.NatsRuntimeSettings("nats://localhost", client_name=" ")

    connection = _NatsConnection()
    client = nats_runtime.NatsPyJetStreamClient(
        cast(Any, connection.jetstream_client),
        cast(Any, connection),
    )
    assert asyncio.run(client.publish("subject", b"data")) == (
        "subject",
        b"data",
        None,
    )
    asyncio.run(client.close())
    assert connection.closed
    with pytest.raises(NatsOutboxError):
        asyncio.run(client.publish("", b"data"))
    with pytest.raises(NatsOutboxError):
        asyncio.run(client.publish("subject", "data"))  # type: ignore[arg-type]


def test_nats_connection_boundary_validates_external_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def connector(**_kwargs: object) -> _NatsConnection:
        return _NatsConnection()

    with pytest.raises(TypeError):
        asyncio.run(nats_runtime.connect_nats_jetstream(object()))  # type: ignore[arg-type]

    class _NoJetStream:
        pass

    async def no_jetstream(**_kwargs: object) -> _NoJetStream:
        return _NoJetStream()

    with pytest.raises(NatsOutboxError, match="JetStream"):
        asyncio.run(
            nats_runtime.connect_nats_jetstream(
                nats_runtime.NatsRuntimeSettings("nats://localhost"),
                connector=cast(Any, no_jetstream),
            )
        )

    module = SimpleNamespace(
        connect=lambda **kwargs: _async_return(_NatsConnection(), kwargs)
    )
    monkeypatch.setattr(nats_runtime, "import_module", lambda _name: module)
    connection = asyncio.run(
        nats_runtime._default_connector(servers=["nats://localhost"], name="test")
    )
    assert isinstance(connection, _NatsConnection)
    monkeypatch.setattr(nats_runtime, "import_module", lambda _name: SimpleNamespace())
    with pytest.raises(NatsOutboxError, match="factory"):
        asyncio.run(
            nats_runtime._default_connector(servers=["nats://localhost"], name="test")
        )


async def _async_return(value: object, _kwargs: object) -> object:
    return value


def test_postgres_runtime_normalizes_dsn_and_closes_initialized_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def connector(dsn: str) -> object:
        calls.append(dsn)
        return object()

    factory = postgres_runtime.postgres_connection_factory(
        "  postgres://db  ", connector=cast(Any, connector)
    )
    factory()
    assert calls == ["postgres://db"]
    with pytest.raises(ValueError):
        postgres_runtime.postgres_connection_factory(" ")

    class _Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

    connection = _Connection()

    class _Runner:
        def apply(self, value: object) -> tuple[int, ...]:
            assert value is connection
            return (1, 2)

    monkeypatch.setattr(postgres_runtime, "MigrationRunner", _Runner)
    assert postgres_runtime.initialize_postgres(cast(Any, lambda: connection)) == (
        1,
        2,
    )
    assert connection.closed


def test_postgres_default_connector_is_lazy_and_validates_driver_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    module = SimpleNamespace(connect=lambda dsn, autocommit: connection)
    monkeypatch.setattr(postgres_runtime, "import_module", lambda _name: module)
    assert postgres_runtime._default_connector("postgres://db") is connection
    monkeypatch.setattr(
        postgres_runtime, "import_module", lambda _name: SimpleNamespace()
    )
    with pytest.raises(RuntimeError, match="connect"):
        postgres_runtime._default_connector("postgres://db")


class _BlobStore(ObjectStore):
    def __init__(self) -> None:
        self.data: dict[str, tuple[ObjectRef, bytes]] = {}

    def put(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        name: str,
        media_type: str,
        data: bytes,
    ) -> ObjectRef:
        digest = sha256(data).hexdigest()
        reference = ObjectRef(
            tenant_id,
            project_id,
            name,
            media_type,
            len(data),
            digest,
            storage_key_for(tenant_id, project_id, digest),
        )
        self.data[reference.storage_key] = (reference, data)
        return reference

    def read(self, tenant_id: UUID, project_id: UUID, reference: ObjectRef) -> bytes:
        if (reference.tenant_id, reference.project_id) != (tenant_id, project_id):
            raise ObjectIntegrityError("scope")
        try:
            return self.data[reference.storage_key][1]
        except KeyError as error:
            raise ObjectNotFound("missing") from error

    def delete(self, tenant_id: UUID, project_id: UUID, reference: ObjectRef) -> None:
        self.read(tenant_id, project_id, reference)
        del self.data[reference.storage_key]


class _References:
    def __init__(self, *, mode: str = "normal") -> None:
        self.values: dict[str, ObjectRef] = {}
        self.mode = mode

    def record(self, reference: ObjectRef) -> ObjectRef:
        if self.mode == "error":
            raise RuntimeError("record failed")
        if self.mode == "mismatch":
            return ObjectRef(
                reference.tenant_id,
                reference.project_id,
                f"{reference.name}-other",
                reference.media_type,
                reference.byte_count,
                reference.sha256,
                reference.storage_key,
            )
        self.values[reference.sha256] = reference
        return reference

    def get(self, tenant_id: UUID, project_id: UUID, digest: str) -> ObjectRef:
        try:
            value = self.values[digest]
        except KeyError as error:
            raise ObjectReferenceNotFound("missing") from error
        if (value.tenant_id, value.project_id) != (tenant_id, project_id):
            raise ObjectReferenceNotFound("missing")
        return value

    def remove(self, reference: ObjectRef) -> None:
        if self.mode == "error":
            raise RuntimeError("remove failed")
        del self.values[reference.sha256]


def test_durable_s3_store_requires_exact_durable_references() -> None:
    with pytest.raises(TypeError):
        DurableS3ObjectStore(object(), _References())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        DurableS3ObjectStore(_BlobStore(), object())  # type: ignore[arg-type]

    blobs = _BlobStore()
    references = _References()
    store = DurableS3ObjectStore(blobs, references)
    reference = store.put(
        TENANT, PROJECT, name="episode", media_type="audio/wav", data=DATA
    )
    assert store.read(TENANT, PROJECT, reference) == DATA
    store.delete(TENANT, PROJECT, reference)
    assert references.values == {}

    unrecorded = blobs.put(
        TENANT, PROJECT, name="episode", media_type="audio/wav", data=DATA
    )
    with pytest.raises(ObjectNotFound, match="reference"):
        store.read(TENANT, PROJECT, unrecorded)

    references.record(reference)
    other_reference = ObjectRef(
        TENANT,
        PROJECT,
        "other",
        "audio/wav",
        len(DATA),
        reference.sha256,
        reference.storage_key,
    )
    with pytest.raises(ObjectIntegrityError, match="reference"):
        store.read(TENANT, PROJECT, other_reference)

    mismatch = DurableS3ObjectStore(blobs, _References(mode="mismatch"))
    with pytest.raises(ObjectIntegrityError, match="reference"):
        mismatch.put(TENANT, PROJECT, name="episode", media_type="audio/wav", data=DATA)

    failed_record = DurableS3ObjectStore(blobs, _References(mode="error"))
    with pytest.raises(ObjectStorageError, match="persistence"):
        failed_record.put(
            TENANT, PROJECT, name="episode", media_type="audio/wav", data=b"other"
        )


def test_durable_s3_store_reports_reference_cleanup_failure() -> None:
    blobs = _BlobStore()
    references = _References()
    store = DurableS3ObjectStore(blobs, references)
    reference = store.put(
        TENANT, PROJECT, name="episode", media_type="audio/wav", data=DATA
    )
    references.mode = "error"
    with pytest.raises(ObjectStorageError, match="cleanup"):
        store.delete(TENANT, PROJECT, reference)
