"""Unit coverage for the injected JetStream outbox boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest

from poddown.nats_outbox import (
    NATS_MESSAGE_ID_HEADER,
    NATS_OUTBOX_STREAM,
    NatsJetStreamOutbox,
    OutboxError,
    OutboxEvent,
    OutboxPublishError,
)

_TENANT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")


@dataclass
class _Delivery:
    payload: bytes
    message_id: str
    acknowledged: bool = False

    async def ack(self) -> None:
        self.acknowledged = True


class _InMemoryJetStream:
    """Small deterministic transport double; it never opens a network connection."""

    def __init__(
        self,
        *,
        failures_before_success: int = 0,
        ensure_failures_before_success: int = 0,
    ) -> None:
        self.failures_before_success = failures_before_success
        self.ensure_failures_before_success = ensure_failures_before_success
        self.ensure_attempts = 0
        self.publish_attempts = 0
        self.streams: dict[str, tuple[str, ...]] = {}
        self.deliveries: list[_Delivery] = []
        self.pulled_subject: str | None = None

    async def ensure_stream(self, *, name: str, subjects: tuple[str, ...]) -> None:
        self.ensure_attempts += 1
        if self.ensure_attempts <= self.ensure_failures_before_success:
            raise RuntimeError("temporary stream setup outage")
        self.streams[name] = subjects

    async def publish(
        self, *, subject: str, payload: bytes, headers: Mapping[str, str]
    ) -> None:
        self.publish_attempts += 1
        if self.publish_attempts <= self.failures_before_success:
            raise RuntimeError("temporary JetStream outage")
        message_id = headers[NATS_MESSAGE_ID_HEADER]
        if not any(item.message_id == message_id for item in self.deliveries):
            self.deliveries.append(_Delivery(payload, message_id))

    async def pull(
        self, *, stream: str, durable_name: str, subject: str, batch_size: int
    ) -> tuple[_Delivery, ...]:
        del stream, durable_name
        self.pulled_subject = subject
        return tuple(item for item in self.deliveries if not item.acknowledged)[
            :batch_size
        ]


def _event(
    *,
    event_id: str = "evt-render-001",
    tenant_id: UUID = _TENANT_ID,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type="render.completed",
        occurred_at=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
        payload={"job_id": "0198d59c-b420-70ce-ae46-786eeeb387e6", "status": "ready"},
    )


def test_publish_retries_a_bounded_number_of_times_with_a_stable_identity() -> None:
    """Catch retries that exceed the configured bound or change event identity."""
    transport = _InMemoryJetStream(failures_before_success=2)
    outbox = NatsJetStreamOutbox(transport, max_publish_attempts=3)

    published = asyncio.run(outbox.publish(_event()))

    assert (
        published.subject
        == "poddown.outbox.v1.0198d59c-b420-70ce-ae46-786eeeb387e1.render.completed"
    )
    assert transport.publish_attempts == 3
    assert transport.streams == {NATS_OUTBOX_STREAM: ("poddown.outbox.v1.>",)}
    assert len(transport.deliveries) == 1
    assert transport.deliveries[0].message_id == "evt-render-001"
    assert json.loads(transport.deliveries[0].payload) == {
        "event_id": "evt-render-001",
        "event_type": "render.completed",
        "occurred_at": "2026-08-12T14:30:00+00:00",
        "payload": {
            "job_id": "0198d59c-b420-70ce-ae46-786eeeb387e6",
            "status": "ready",
        },
        "tenant_id": "0198d59c-b420-70ce-ae46-786eeeb387e1",
    }


def test_publish_surfaces_failure_after_the_configured_attempt_bound() -> None:
    """Catch a publisher that retries indefinitely during a transport outage."""
    transport = _InMemoryJetStream(failures_before_success=3)
    outbox = NatsJetStreamOutbox(transport, max_publish_attempts=2)

    with pytest.raises(OutboxPublishError, match="after 2 attempts"):
        asyncio.run(outbox.publish(_event()))

    assert transport.publish_attempts == 2
    assert transport.deliveries == []


def test_publish_retries_stream_setup_within_the_configured_attempt_bound() -> None:
    """Catch stream setup failures that bypass the publisher retry contract."""
    transport = _InMemoryJetStream(ensure_failures_before_success=2)
    outbox = NatsJetStreamOutbox(transport, max_publish_attempts=3)

    asyncio.run(outbox.publish(_event()))

    assert transport.ensure_attempts == 3
    assert transport.publish_attempts == 1


def test_publish_surfaces_stream_setup_failure_after_the_configured_attempt_bound() -> (
    None
):
    """Catch an unavailable stream setup path that retries without a bound."""
    transport = _InMemoryJetStream(ensure_failures_before_success=3)
    outbox = NatsJetStreamOutbox(transport, max_publish_attempts=2)

    with pytest.raises(OutboxPublishError, match="after 2 attempts"):
        asyncio.run(outbox.publish(_event()))

    assert transport.ensure_attempts == 2
    assert transport.publish_attempts == 0


def test_replay_acknowledges_only_after_the_handler_accepts_the_event() -> None:
    """Catch a replay path that loses a delivery when its handler fails."""
    transport = _InMemoryJetStream()
    outbox = NatsJetStreamOutbox(transport)
    asyncio.run(outbox.publish(_event()))
    accepted: list[OutboxEvent] = []

    async def reject_once(event: OutboxEvent) -> None:
        accepted.append(event)
        raise RuntimeError("downstream unavailable")

    with pytest.raises(RuntimeError, match="downstream unavailable"):
        asyncio.run(
            outbox.replay_once("billing-v1", reject_once, tenant_id=_event().tenant_id)
        )
    assert transport.deliveries[0].acknowledged is False

    async def accept(event: OutboxEvent) -> None:
        accepted.append(event)

    replayed = asyncio.run(
        outbox.replay_once("billing-v1", accept, tenant_id=_event().tenant_id)
    )

    assert replayed == 1
    assert [item.event_id for item in accepted] == ["evt-render-001", "evt-render-001"]
    assert transport.deliveries[0].acknowledged is True


def test_replay_rejects_cross_tenant_delivery_before_handler_or_ack() -> None:
    """Catch a forged delivery escaping the tenant-scoped durable consumer."""
    requested_tenant = _event().tenant_id
    foreign_event = _event(
        event_id="evt-render-foreign",
        tenant_id=UUID("0198d59c-b420-70ce-ae46-786eeeb387e2"),
    )
    transport = _InMemoryJetStream()
    transport.deliveries.append(
        _Delivery(foreign_event.to_wire(), foreign_event.event_id)
    )
    outbox = NatsJetStreamOutbox(transport)
    handled: list[OutboxEvent] = []

    async def handler(event: OutboxEvent) -> None:
        handled.append(event)

    with pytest.raises(OutboxError, match="tenant does not match replay scope"):
        asyncio.run(
            outbox.replay_once("billing-v1", handler, tenant_id=requested_tenant)
        )

    assert handled == []
    assert transport.deliveries[0].acknowledged is False
    assert transport.pulled_subject == f"poddown.outbox.v1.{requested_tenant}.>"
