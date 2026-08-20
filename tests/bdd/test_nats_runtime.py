"""BDD bindings for the concrete nats-py JetStream transport."""

from __future__ import annotations

import asyncio
from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.nats_runtime import NatsRuntimeSettings, connect_nats_jetstream

scenarios("../features/nats_runtime.feature")


class _JetStream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, dict[str, str] | None]] = []

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        self.calls.append((subject, payload, headers))
        return {"stream": "PODDOWN_EVENTS", "seq": "1"}


class _Connection:
    def __init__(self) -> None:
        self.jetstream_client = _JetStream()
        self.closed = False

    def jetstream(self) -> _JetStream:
        return self.jetstream_client

    async def close(self) -> None:
        self.closed = True


@given("a fake NATS connection")
def fake_nats_connection(context: Any) -> None:
    connection = _Connection()

    async def connector(*, servers: list[str], name: str) -> _Connection:
        context.values["servers"] = servers
        context.values["name"] = name
        return connection

    context.values["connection"] = connection
    context.values["client"] = asyncio.run(
        connect_nats_jetstream(
            NatsRuntimeSettings(address="nats://localhost:4222", client_name="poddown"),
            connector=connector,
        )
    )


@when("I publish a message through the NATS runtime transport")
def publish_nats(context: Any) -> None:
    context.values["ack"] = asyncio.run(
        context.values["client"].publish(
            "poddown.events.episode.created",
            b'{"event":"created"}',
            headers={"Nats-Msg-Id": "event-1"},
        )
    )


@then("the NATS subject and acknowledgement are preserved")
def nats_publish_preserved(context: Any) -> None:
    client = context.values["client"]
    assert context.values["ack"] == {"stream": "PODDOWN_EVENTS", "seq": "1"}
    assert context.values["servers"] == ["nats://localhost:4222"]
    assert client._connection.jetstream_client.calls == [
        (
            "poddown.events.episode.created",
            b'{"event":"created"}',
            {"Nats-Msg-Id": "event-1"},
        )
    ]
