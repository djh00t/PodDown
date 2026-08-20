"""NATS JetStream delivery for the tenant-scoped transactional outbox."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Thread
from typing import Any, Protocol, cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from poddown.outbox import OutboxConnection, OutboxEvent, PostgresOutbox

_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OUTBOX_RELAY_ACTIVITY_NAME = "relay_outbox"
OUTBOX_RELAY_WORKFLOW_NAME = "OutboxRelayWorkflow"
OUTBOX_RELAY_ACTIVITY_TIMEOUT = timedelta(minutes=5)


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


def _payload(event: OutboxEvent) -> bytes:
    return json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _subject_prefix(value: str) -> str:
    if _SUBJECT.fullmatch(value) is None:
        raise NatsOutboxError("subject_prefix is invalid")
    return value


def _message(
    event: OutboxEvent, subject_prefix: str
) -> tuple[str, bytes, dict[str, str]]:
    if not isinstance(event, OutboxEvent):
        raise NatsOutboxError("event must be an OutboxEvent")
    subject = f"{subject_prefix}.{event.event_type}"
    if _SUBJECT.fullmatch(subject) is None:
        raise NatsOutboxError("event type cannot form a NATS subject")
    return (
        subject,
        _payload(event),
        {
            "Nats-Msg-Id": str(event.event_id),
            "Poddown-Tenant-Id": str(event.tenant_id),
            "Poddown-Project-Id": str(event.project_id),
        },
    )


async def _publish_message(
    client: NatsJetStreamClient,
    event: OutboxEvent,
    subject_prefix: str,
) -> None:
    subject, payload, headers = _message(event, subject_prefix)
    try:
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
        subject_prefix: str = "poddown.events",
    ) -> None:
        if not hasattr(client, "publish"):
            raise TypeError("client must expose async publish")
        subject_prefix = _subject_prefix(subject_prefix)
        self._client = client
        self._subject_prefix = subject_prefix

    def publish(self, event: OutboxEvent) -> None:
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
        subject_prefix: str = "poddown.events",
    ) -> None:
        if not hasattr(client, "publish"):
            raise TypeError("client must expose async publish")
        self._client = client
        self._subject_prefix = _subject_prefix(subject_prefix)

    async def publish(self, event: OutboxEvent) -> None:
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
    subject_prefix: str = "poddown.events",
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
