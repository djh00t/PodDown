"""Concrete nats-py JetStream connection boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast
from urllib.parse import urlsplit

from poddown.nats_outbox import NatsJetStreamClient, NatsOutboxError


class NatsJetStreamPort(Protocol):
    """Minimal nats-py JetStream surface used by the event publisher."""

    async def stream_info(self, name: str) -> object:
        """Return one stream description."""

    async def add_stream(self, **kwargs: object) -> object:
        """Create one stream."""

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Publish one message and await its acknowledgement."""


class NatsConnection(Protocol):
    """Minimal nats-py connection surface needed for lifecycle management."""

    def jetstream(self) -> NatsJetStreamPort:
        """Return the JetStream context."""

    async def close(self) -> None:
        """Close the client connection."""


NatsConnector = Callable[..., Awaitable[NatsConnection]]


@dataclass(frozen=True, slots=True)
class NatsRuntimeSettings:
    """Non-secret NATS connection settings."""

    address: str
    client_name: str = "poddown"

    def __post_init__(self) -> None:
        if not isinstance(self.address, str) or not self.address.strip():
            raise ValueError("NATS address must be non-empty")
        normalized = self.address.strip()
        parsed = urlsplit(normalized if "://" in normalized else f"nats://{normalized}")
        if parsed.scheme not in {"nats", "tls"} or not parsed.hostname:
            raise ValueError("NATS address must use nats:// or tls://")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("NATS credentials must not be embedded in the address")
        if not isinstance(self.client_name, str) or not self.client_name.strip():
            raise ValueError("NATS client_name must be non-empty")
        object.__setattr__(self, "address", normalized)
        object.__setattr__(self, "client_name", self.client_name.strip())


async def _default_connector(*, servers: list[str], name: str) -> NatsConnection:
    """Load nats-py lazily at the external network boundary."""
    module = import_module("nats")
    connect = getattr(module, "connect", None)
    if not callable(connect):
        raise NatsOutboxError("nats-py connection factory is unavailable")
    return cast(NatsConnection, await connect(servers=servers, name=name))


class NatsPyJetStreamClient(NatsJetStreamClient):
    """Adapt a live nats-py JetStream context to PodDown's publisher port."""

    def __init__(
        self,
        jetstream: NatsJetStreamPort,
        connection: NatsConnection,
    ) -> None:
        self._jetstream = jetstream
        self._connection = connection

    async def ensure_stream(self, *, name: str, subjects: tuple[str, ...]) -> None:
        """Create the stream once, or fail closed on a subject mismatch."""
        if (
            not name.strip()
            or not subjects
            or any(not item.strip() for item in subjects)
        ):
            raise NatsOutboxError("NATS stream configuration is invalid")
        try:
            info = await self._jetstream.stream_info(name)
        except Exception:
            try:
                await self._jetstream.add_stream(
                    name=name,
                    subjects=list(subjects),
                )
            except Exception as error:
                raise NatsOutboxError("NATS stream setup failed") from error
            return
        config = getattr(info, "config", None)
        configured_subjects = getattr(config, "subjects", None)
        if (
            not isinstance(configured_subjects, (list, tuple))
            or tuple(configured_subjects) != subjects
        ):
            raise NatsOutboxError("NATS stream subjects do not match")

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Publish one event through the live JetStream context."""
        if not isinstance(subject, str) or not subject:
            raise NatsOutboxError("NATS subject is invalid")
        if type(payload) is not bytes:
            raise NatsOutboxError("NATS payload must be bytes")
        return await self._jetstream.publish(
            subject,
            payload,
            headers=dict(headers) if headers is not None else None,
        )

    async def close(self) -> None:
        """Close the underlying NATS connection."""
        await self._connection.close()


async def connect_nats_jetstream(
    settings: NatsRuntimeSettings,
    *,
    connector: NatsConnector | None = None,
) -> NatsPyJetStreamClient:
    """Connect one JetStream client without making NATS workflow authority."""
    if not isinstance(settings, NatsRuntimeSettings):
        raise TypeError("settings must be NatsRuntimeSettings")
    connection = await (connector or _default_connector)(
        servers=[settings.address],
        name=settings.client_name,
    )
    jetstream_method = getattr(connection, "jetstream", None)
    if not callable(jetstream_method):
        raise NatsOutboxError("NATS connection has no JetStream context")
    return NatsPyJetStreamClient(jetstream_method(), connection)


__all__ = [
    "NatsConnection",
    "NatsConnector",
    "NatsJetStreamPort",
    "NatsPyJetStreamClient",
    "NatsRuntimeSettings",
    "connect_nats_jetstream",
]
