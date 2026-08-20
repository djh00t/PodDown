"""NATS JetStream delivery for the tenant-scoped transactional outbox."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Thread
from typing import Any, Protocol, cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from poddown.outbox import (
    OutboxConnection,
    PostgresOutbox,
)
from poddown.outbox import (
    OutboxEvent as PostgresOutboxEvent,
)

_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OUTBOX_RELAY_ACTIVITY_NAME = "relay_outbox"
OUTBOX_RELAY_WORKFLOW_NAME = "OutboxRelayWorkflow"
OUTBOX_RELAY_ACTIVITY_TIMEOUT = timedelta(minutes=5)
NATS_OUTBOX_STREAM = "PODDOWN_OUTBOX_V1"
NATS_OUTBOX_SUBJECT_PREFIX = "poddown.outbox.v1"
NATS_MESSAGE_ID_HEADER = "Nats-Msg-Id"


class NatsOutboxError(ValueError):
    """Base error for NATS outbox configuration and delivery failures."""


class NatsPublishError(NatsOutboxError, RuntimeError):
    """A JetStream publish did not complete successfully."""


OUTBOX_RELAY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    non_retryable_error_types=(NatsOutboxError.__name__,),
)


class NatsJetStreamClient(Protocol):
    """Minimal async JetStream publish surface."""

    async def ensure_stream(self, *, name: str, subjects: tuple[str, ...]) -> None:
        """Create or validate the durable stream before publishing."""

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Publish one message and await the server acknowledgement."""


class ClosableNatsJetStreamClient(NatsJetStreamClient, Protocol):
    """JetStream client that owns a closeable NATS connection."""

    async def close(self) -> None:
        """Close the underlying NATS connection."""


NatsClientFactory = Callable[[], Awaitable[ClosableNatsJetStreamClient]]


class TransactionalOutboxConnection(OutboxConnection, Protocol):
    """Outbox connection with the transaction lifecycle owned by the caller."""

    def transaction(self) -> AbstractContextManager[object]:
        """Return the surrounding transaction context."""


OutboxConnectionFactory = Callable[[], TransactionalOutboxConnection]


def _run_coroutine(
    operation: Callable[[], Coroutine[object, object, object]],
) -> object:
    """Bridge an async NATS client to synchronous outbox relay workers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(operation())

    result: list[object] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(operation()))
        except BaseException as error:  # pragma: no cover - re-raised below
            errors.append(error)

    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    if not result:
        raise RuntimeError("NATS publish returned no result")
    return result[0]


def _payload(event: PostgresOutboxEvent) -> bytes:
    return json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _subject_prefix(value: str) -> str:
    if _SUBJECT.fullmatch(value) is None:
        raise NatsOutboxError("subject_prefix is invalid")
    return value


def _message(
    event: PostgresOutboxEvent, subject_prefix: str
) -> tuple[str, bytes, dict[str, str]]:
    if not isinstance(event, PostgresOutboxEvent):
        raise NatsOutboxError("event must be a PostgresOutboxEvent")
    subject = f"{subject_prefix}.{event.tenant_id}.{event.event_type}"
    if _SUBJECT.fullmatch(subject) is None:
        raise NatsOutboxError("event type cannot form a NATS subject")
    return (
        subject,
        _payload(event),
        {
            NATS_MESSAGE_ID_HEADER: str(event.event_id),
            "Poddown-Tenant-Id": str(event.tenant_id),
            "Poddown-Project-Id": str(event.project_id),
        },
    )


async def _publish_message(
    client: NatsJetStreamClient,
    event: PostgresOutboxEvent,
    subject_prefix: str,
) -> None:
    subject, payload, headers = _message(event, subject_prefix)
    try:
        await client.ensure_stream(
            name=NATS_OUTBOX_STREAM,
            subjects=(f"{subject_prefix}.>",),
        )
        await client.publish(subject, payload, headers=headers)
    except Exception as error:
        if isinstance(error, NatsOutboxError):
            raise
        raise NatsPublishError("NATS JetStream publish failed") from error


class NatsJetStreamPublisher:
    """Publish immutable outbox events with JetStream duplicate suppression."""

    def __init__(
        self,
        client: NatsJetStreamClient,
        *,
        subject_prefix: str = NATS_OUTBOX_SUBJECT_PREFIX,
    ) -> None:
        if not hasattr(client, "publish"):
            raise TypeError("client must expose async publish")
        subject_prefix = _subject_prefix(subject_prefix)
        self._client = client
        self._subject_prefix = subject_prefix

    def publish(self, event: PostgresOutboxEvent) -> None:
        """Publish one event using its UUID as the JetStream deduplication key."""
        try:
            _run_coroutine(
                lambda: _publish_message(self._client, event, self._subject_prefix)
            )
        except Exception as error:
            if isinstance(error, NatsOutboxError):
                raise
            raise NatsPublishError("NATS JetStream publish failed") from error


class AsyncNatsJetStreamPublisher:
    """Publish outbox events on the caller's existing async NATS loop."""

    def __init__(
        self,
        client: NatsJetStreamClient,
        *,
        subject_prefix: str = NATS_OUTBOX_SUBJECT_PREFIX,
    ) -> None:
        if not hasattr(client, "publish"):
            raise TypeError("client must expose async publish")
        self._client = client
        self._subject_prefix = _subject_prefix(subject_prefix)

    async def publish(self, event: PostgresOutboxEvent) -> None:
        """Publish without crossing event-loop or thread boundaries."""
        await _publish_message(self._client, event, self._subject_prefix)


@dataclass(frozen=True, slots=True)
class OutboxRelayReport:
    """Bounded delivery results for one tenant-scoped relay pass."""

    published_event_ids: tuple[UUID, ...]
    failed_event_ids: tuple[UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a compact activity result without event payloads."""
        return {
            "published_event_ids": [str(value) for value in self.published_event_ids],
            "failed_event_ids": [str(value) for value in self.failed_event_ids],
        }


@dataclass(frozen=True, slots=True)
class OutboxRelayRequest:
    """Validated tenant-scoped input for one bounded relay activity."""

    tenant_id: UUID
    limit: int = 100

    def __post_init__(self) -> None:
        if self.tenant_id.version != 7:
            raise NatsOutboxError("tenant_id must be a UUIDv7")
        if type(self.limit) is not int or not 0 < self.limit <= 1000:
            raise NatsOutboxError("limit must be between 1 and 1000")

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> OutboxRelayRequest:
        """Decode and validate a secret-free activity payload."""
        if not isinstance(payload, Mapping):
            raise NatsOutboxError("relay payload must be an object")
        try:
            tenant_id = UUID(str(payload["tenant_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise NatsOutboxError("relay tenant_id is invalid") from error
        limit = payload.get("limit", 100)
        if type(limit) is not int:
            raise NatsOutboxError("relay limit is invalid")
        return cls(tenant_id=tenant_id, limit=limit)


@workflow.defn(name=OUTBOX_RELAY_WORKFLOW_NAME)
class OutboxRelayWorkflow:
    """Run one bounded tenant relay through a durable Temporal invocation."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a tenant batch and invoke the registered relay activity."""
        request = OutboxRelayRequest.from_payload(payload)
        relay_payload = {"tenant_id": str(request.tenant_id), "limit": request.limit}
        result = await workflow.execute_activity(
            OUTBOX_RELAY_ACTIVITY_NAME,
            args=[relay_payload],
            start_to_close_timeout=OUTBOX_RELAY_ACTIVITY_TIMEOUT,
            retry_policy=OUTBOX_RELAY_RETRY_POLICY,
            activity_id=(
                f"{OUTBOX_RELAY_WORKFLOW_NAME}:{request.tenant_id}:{request.limit}"
            ),
        )
        if not isinstance(result, dict):
            raise NatsOutboxError("relay activity returned a non-object report")
        return cast(dict[str, Any], result)


class PostgresOutboxRelay:
    """Deliver a locked outbox batch and preserve retry evidence on failure."""

    def __init__(
        self,
        outbox: PostgresOutbox | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._outbox = outbox or PostgresOutbox()
        self._clock = clock

    def relay(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        publisher: NatsJetStreamPublisher,
        *,
        limit: int = 100,
    ) -> OutboxRelayReport:
        """Publish pending events and acknowledge only successful deliveries."""
        events = self._outbox.pending(connection, tenant_id, limit=limit)
        published: list[UUID] = []
        failed: list[UUID] = []
        for event in events:
            try:
                publisher.publish(event)
            except Exception:
                self._outbox.record_attempt(connection, tenant_id, event.event_id)
                failed.append(event.event_id)
            else:
                self._outbox.mark_published(
                    connection,
                    tenant_id,
                    event.event_id,
                    self._clock(),
                )
                published.append(event.event_id)
        return OutboxRelayReport(tuple(published), tuple(failed))

    async def relay_async(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        publisher: AsyncNatsJetStreamPublisher,
        *,
        limit: int = 100,
    ) -> OutboxRelayReport:
        """Relay through a NATS client bound to the current async event loop."""
        events = self._outbox.pending(connection, tenant_id, limit=limit)
        published: list[UUID] = []
        failed: list[UUID] = []
        for event in events:
            try:
                await publisher.publish(event)
            except Exception:
                self._outbox.record_attempt(connection, tenant_id, event.event_id)
                failed.append(event.event_id)
            else:
                self._outbox.mark_published(
                    connection,
                    tenant_id,
                    event.event_id,
                    self._clock(),
                )
                published.append(event.event_id)
        return OutboxRelayReport(tuple(published), tuple(failed))


def build_nats_outbox_relay_activity(
    connection_factory: OutboxConnectionFactory,
    client_factory: NatsClientFactory,
    *,
    subject_prefix: str = NATS_OUTBOX_SUBJECT_PREFIX,
    relay: PostgresOutboxRelay | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build an opt-in Temporal activity for one bounded outbox relay pass."""
    if not callable(connection_factory) or not callable(client_factory):
        raise TypeError("relay factories must be callable")
    validated_prefix = _subject_prefix(subject_prefix)
    effective_relay = relay or PostgresOutboxRelay()

    @activity.defn(name=OUTBOX_RELAY_ACTIVITY_NAME)
    async def relay_outbox(payload: dict[str, Any]) -> dict[str, Any]:
        request = OutboxRelayRequest.from_payload(payload)
        connection = connection_factory()
        client: ClosableNatsJetStreamClient | None = None
        try:
            client = await client_factory()
            publisher = AsyncNatsJetStreamPublisher(
                client,
                subject_prefix=validated_prefix,
            )
            with connection.transaction():
                report = await effective_relay.relay_async(
                    connection,
                    request.tenant_id,
                    publisher,
                    limit=request.limit,
                )
            return report.to_dict()
        finally:
            if client is not None:
                await client.close()
            close = getattr(connection, "close", None)
            if callable(close):
                close()

    return relay_outbox


__all__ = [
    "AsyncNatsJetStreamPublisher",
    "ClosableNatsJetStreamClient",
    "NATS_OUTBOX_STREAM",
    "NATS_OUTBOX_SUBJECT_PREFIX",
    "NatsClientFactory",
    "NatsJetStreamClient",
    "NatsJetStreamPublisher",
    "NatsOutboxError",
    "NatsPublishError",
    "OUTBOX_RELAY_ACTIVITY_TIMEOUT",
    "OUTBOX_RELAY_ACTIVITY_NAME",
    "OUTBOX_RELAY_RETRY_POLICY",
    "OUTBOX_RELAY_WORKFLOW_NAME",
    "TransactionalOutboxConnection",
    "OutboxRelayWorkflow",
    "OutboxRelayRequest",
    "OutboxConnectionFactory",
    "OutboxRelayReport",
    "PostgresOutboxRelay",
    "build_nats_outbox_relay_activity",
]


class JetStreamDelivery(Protocol):
    """One durable JetStream delivery that can be acknowledged after handling."""

    payload: bytes
    message_id: str

    async def ack(self) -> None: ...


class JetStreamTransport(Protocol):
    """Small async boundary that an authenticated NATS client can implement."""

    async def ensure_stream(self, *, name: str, subjects: tuple[str, ...]) -> None: ...

    async def publish(
        self, *, subject: str, payload: bytes, headers: Mapping[str, str]
    ) -> None: ...

    async def pull(
        self, *, stream: str, durable_name: str, subject: str, batch_size: int
    ) -> Sequence[JetStreamDelivery]: ...


class OutboxError(RuntimeError):
    """Base error raised by the NATS outbox adapter."""


class OutboxPublishError(OutboxError):
    """An event could not be accepted by JetStream within the attempt bound."""


@dataclass(frozen=True)
class OutboxEvent:
    """Immutable application event with a caller-owned replay identity."""

    event_id: str
    tenant_id: UUID
    event_type: str
    occurred_at: datetime
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        if not self.event_type or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in self.event_type
        ):
            raise ValueError("event_type must use lowercase NATS-safe characters")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    @property
    def subject(self) -> str:
        """Return the tenant-scoped, explicitly versioned NATS subject."""
        return f"{NATS_OUTBOX_SUBJECT_PREFIX}.{self.tenant_id}.{self.event_type}"

    def to_wire(self) -> bytes:
        """Serialize stable event evidence without transport-specific fields."""
        return json.dumps(
            {
                "event_id": self.event_id,
                "event_type": self.event_type,
                "occurred_at": self.occurred_at.isoformat(),
                "payload": dict(self.payload),
                "tenant_id": str(self.tenant_id),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    @classmethod
    def from_wire(cls, payload: bytes) -> OutboxEvent:
        """Parse one delivery and reject malformed immutable event evidence."""
        try:
            value = json.loads(payload)
            if not isinstance(value, dict) or not isinstance(
                value.get("payload"), dict
            ):
                raise ValueError("outbox payload is invalid")
            return cls(
                event_id=str(value["event_id"]),
                tenant_id=UUID(str(value["tenant_id"])),
                event_type=str(value["event_type"]),
                occurred_at=datetime.fromisoformat(str(value["occurred_at"])),
                payload=value["payload"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OutboxError("outbox delivery is invalid") from error


class NatsJetStreamOutbox:
    """Publish and replay immutable events through an injected JetStream transport."""

    def __init__(
        self, transport: JetStreamTransport, *, max_publish_attempts: int = 3
    ) -> None:
        if max_publish_attempts < 1:
            raise ValueError("max_publish_attempts must be at least one")
        self._transport = transport
        self._max_publish_attempts = max_publish_attempts

    async def publish(self, event: OutboxEvent) -> OutboxEvent:
        """Publish an event with deduplication identity and bounded retries."""
        for attempt in range(1, self._max_publish_attempts + 1):
            try:
                await self._transport.ensure_stream(
                    name=NATS_OUTBOX_STREAM,
                    subjects=(f"{NATS_OUTBOX_SUBJECT_PREFIX}.>",),
                )
                await self._transport.publish(
                    subject=event.subject,
                    payload=event.to_wire(),
                    headers={NATS_MESSAGE_ID_HEADER: event.event_id},
                )
                return event
            except Exception as error:
                if attempt == self._max_publish_attempts:
                    raise OutboxPublishError(
                        f"outbox publish failed after {attempt} attempts"
                    ) from error
        raise AssertionError("unreachable")

    async def replay_once(
        self,
        durable_name: str,
        handler: Callable[[OutboxEvent], Awaitable[None]],
        *,
        tenant_id: UUID,
        batch_size: int = 100,
    ) -> int:
        """Deliver a durable batch and acknowledge events after handler success."""
        if not durable_name.strip():
            raise ValueError("durable_name is required")
        if batch_size < 1:
            raise ValueError("batch_size must be at least one")
        deliveries = await self._transport.pull(
            stream=NATS_OUTBOX_STREAM,
            durable_name=durable_name,
            subject=f"{NATS_OUTBOX_SUBJECT_PREFIX}.{tenant_id}.>",
            batch_size=batch_size,
        )
        for delivery in deliveries:
            event = OutboxEvent.from_wire(delivery.payload)
            if delivery.message_id != event.event_id:
                raise OutboxError("outbox delivery identity does not match payload")
            if event.tenant_id != tenant_id:
                raise OutboxError("outbox delivery tenant does not match replay scope")
            await handler(event)
            await delivery.ack()
        return len(deliveries)
