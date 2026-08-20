"""Opt-in NATS JetStream integration coverage for the durable outbox boundary."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.nats_outbox import (
    NATS_MESSAGE_ID_HEADER,
    NATS_OUTBOX_STREAM,
    NATS_OUTBOX_SUBJECT_PREFIX,
    NatsJetStreamOutbox,
    NatsOutboxError,
    OutboxEvent,
)
from poddown.nats_runtime import NatsPyJetStreamClient, NatsRuntimeSettings
from tests.bdd.conftest import ScenarioContext

pytestmark = pytest.mark.nats

scenarios("../features/nats_jetstream.feature")

TENANT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")


@pytest.fixture
def context() -> ScenarioContext:
    """Provide BDD state when this opt-in feature is collected as integration."""
    return ScenarioContext()


@dataclass(frozen=True, slots=True)
class NatsIntegrationSettings:
    """Secret-free, loopback-only NATS integration settings."""

    address: str


def _settings_or_skip() -> NatsIntegrationSettings:
    """Require an explicit loopback NATS integration target."""
    if os.getenv("PODDOWN_NATS_TESTS") != "1":
        pytest.skip("set PODDOWN_NATS_TESTS=1 for the local NATS integration")

    address = os.getenv("PODDOWN_NATS_ENDPOINT", "nats://127.0.0.1:4222").strip()
    parsed = urlsplit(address if "://" in address else f"nats://{address}")
    try:
        loopback_host = (
            parsed.hostname is not None and ip_address(parsed.hostname).is_loopback
        )
    except ValueError:
        loopback_host = False
    if parsed.scheme not in {"nats", "tls"} or not loopback_host:
        pytest.skip("NATS integration is restricted to a loopback endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise NatsOutboxError("NATS integration credentials must not be embedded")
    return NatsIntegrationSettings(
        NatsRuntimeSettings(address=address, client_name="poddown-integration").address
    )


def _event() -> OutboxEvent:
    """Create one unique event whose identity is replayed unchanged."""
    return OutboxEvent(
        event_id=f"nats-integration-{uuid4().hex}",
        tenant_id=TENANT_ID,
        event_type="render.completed",
        occurred_at=datetime.now(UTC),
        payload={"status": "ready", "source": "local-integration"},
    )


def _publish_twice_and_inspect(
    settings: NatsIntegrationSettings,
    event: OutboxEvent,
) -> Mapping[str, object]:
    """Publish one event twice and inspect the resulting JetStream record."""

    async def run() -> Mapping[str, object]:
        import nats  # type: ignore[import-untyped]

        connection = await nats.connect(
            servers=[settings.address],
            name=f"poddown-integration-{uuid4().hex}",
        )
        jetstream = connection.jetstream()
        try:
            try:
                before_info = await jetstream.stream_info(NATS_OUTBOX_STREAM)
                before_messages = int(before_info.state.messages)
            except Exception:
                before_messages = 0

            client = NatsPyJetStreamClient(jetstream, connection)
            publisher = NatsJetStreamOutbox(client)
            await publisher.publish(event)
            await publisher.publish(event)

            after_info = await jetstream.stream_info(NATS_OUTBOX_STREAM)
            last_sequence = int(after_info.state.last_seq)
            message = await jetstream.get_msg(
                NATS_OUTBOX_STREAM,
                seq=last_sequence,
            )
            result: dict[str, object] = {
                "before_messages": before_messages,
                "after_messages": int(after_info.state.messages),
                "subject": message.subject,
                "headers": dict(message.headers or {}),
                "payload": json.loads((message.data or b"{}").decode("utf-8")),
                "sequence": last_sequence,
            }
            await jetstream.delete_msg(NATS_OUTBOX_STREAM, last_sequence)
            return result
        finally:
            await connection.close()

    return asyncio.run(run())


def test_nats_jetstream_deduplicates_a_replayed_outbox_event() -> None:
    """A real local JetStream stores one message for two identical publishes."""
    settings = _settings_or_skip()
    event = _event()
    evidence = _publish_twice_and_inspect(settings, event)

    assert evidence["after_messages"] == int(evidence["before_messages"]) + 1
    assert evidence["subject"] == (
        f"{NATS_OUTBOX_SUBJECT_PREFIX}.{TENANT_ID}.{event.event_type}"
    )
    headers = evidence["headers"]
    assert isinstance(headers, Mapping)
    assert headers[NATS_MESSAGE_ID_HEADER] == event.event_id
    payload = evidence["payload"]
    assert isinstance(payload, Mapping)
    assert payload["event_id"] == event.event_id


@given("an opt-in local NATS JetStream outbox")
def local_nats_outbox(context: Any) -> None:
    """Bind the explicitly enabled local NATS target."""
    context.values["settings"] = _settings_or_skip()
    context.values["event"] = _event()


@when("I publish the same outbox event twice")
def publish_replayed_nats_event(context: Any) -> None:
    """Publish the same immutable event identity twice."""
    context.values["evidence"] = _publish_twice_and_inspect(
        context.values["settings"],
        context.values["event"],
    )


@then("the local JetStream stores one message with the stable event identity")
def verify_replayed_nats_event(context: Any) -> None:
    """Verify subject, deduplication, headers, and immutable payload identity."""
    evidence = context.values["evidence"]
    event = context.values["event"]
    assert evidence["after_messages"] == int(evidence["before_messages"]) + 1
    assert evidence["subject"] == (
        f"{NATS_OUTBOX_SUBJECT_PREFIX}.{TENANT_ID}.{event.event_type}"
    )
    headers = evidence["headers"]
    assert isinstance(headers, Mapping)
    assert headers[NATS_MESSAGE_ID_HEADER] == event.event_id
    payload = evidence["payload"]
    assert isinstance(payload, Mapping)
    assert payload["event_id"] == event.event_id
