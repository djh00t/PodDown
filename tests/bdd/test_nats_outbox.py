"""BDD bindings for NATS JetStream transactional-outbox delivery."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when
from uuid6 import uuid7

from poddown.nats_outbox import (
    NATS_OUTBOX_STREAM,
    OUTBOX_RELAY_ACTIVITY_NAME,
    OUTBOX_RELAY_WORKFLOW_NAME,
    NatsJetStreamPublisher,
    OutboxRelayWorkflow,
    PostgresOutboxRelay,
    build_nats_outbox_relay_activity,
)
from poddown.outbox import OutboxEvent, PostgresOutbox

scenarios("../features/nats_outbox.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
AGGREGATE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
CREATED = datetime(2026, 8, 14, tzinfo=UTC)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rows: list[tuple[object, ...]] = []
        self.last: tuple[object, ...] | None = None
        self.rowcount = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.rows = []
        self.last = None
        self.rowcount = 0
        if normalized.startswith("select set_config"):
            return self
        if normalized.startswith("insert into outbox_events"):
            event_id = str(params[2])
            if event_id not in self.connection.events:
                self.connection.events[event_id] = (
                    *params[:8],
                    None,
                    params[8],
                )
                self.rowcount = 1
            return self
        if normalized.startswith("select event_id"):
            if "published_at is null" in normalized:
                self.rows = [
                    self.connection.row(event)
                    for event in self.connection.events.values()
                    if event[-2] is None
                ][: int(params[-1])]
            else:
                event = self.connection.events.get(str(params[1]))
                self.last = self.connection.row(event) if event else None
                self.rows = [self.last] if self.last is not None else []
            return self
        if normalized.startswith("update outbox_events set published_at"):
            event_id = str(params[-1])
            event = self.connection.events[event_id]
            if event[-2] is None:
                values = list(event)
                values[-2] = params[0]
                self.connection.events[event_id] = tuple(values)
                self.rowcount = 1
            return self
        if normalized.startswith("update outbox_events set attempt_count"):
            event_id = str(params[1])
            event = self.connection.events[event_id]
            values = list(event)
            values[-1] = int(values[-1]) + 1
            self.connection.events[event_id] = tuple(values)
            self.rowcount = 1
            return self
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self.last

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.events: dict[str, tuple[object, ...]] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def row(values: tuple[object, ...]) -> tuple[object, ...]:
        (
            _tenant_id,
            project_id,
            event_id,
            aggregate_type,
            aggregate_id,
            event_type,
            payload,
            created_at,
            published_at,
            attempts,
        ) = values
        return (
            UUID(str(event_id)),
            UUID(str(project_id)),
            aggregate_type,
            UUID(str(aggregate_id)),
            event_type,
            payload,
            created_at,
            published_at,
            attempts,
        )


class _Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, bytes, dict[str, str]]] = []
        self.streams: list[tuple[str, tuple[str, ...]]] = []
        self.closed = False

    async def ensure_stream(self, *, name: str, subjects: tuple[str, ...]) -> None:
        self.streams.append((name, subjects))

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> object:
        if self.fail:
            raise RuntimeError("nats unavailable")
        self.messages.append((subject, payload, headers or {}))
        return object()

    async def close(self) -> None:
        self.closed = True


def _event() -> OutboxEvent:
    return OutboxEvent(
        tenant_id=TENANT,
        project_id=PROJECT,
        event_id=uuid7(),
        aggregate_type="episode",
        aggregate_id=AGGREGATE,
        event_type="episode.created",
        payload={"source_sha256": "a" * 64},
        created_at=CREATED,
    )


def _prepare(context: Any, *, fail: bool) -> None:
    connection = _Connection()
    event = _event()
    PostgresOutbox().append(connection, event)
    client = _Client(fail=fail)
    context.values["connection"] = connection
    context.values["event"] = event
    context.values["client"] = client
    context.values["publisher"] = NatsJetStreamPublisher(client)
    context.values["relay"] = PostgresOutboxRelay()


@given("a recording NATS JetStream client and outbox connection")
def recording_nats(context: Any) -> None:
    _prepare(context, fail=False)


@given("a failing NATS JetStream client and outbox connection")
def failing_nats(context: Any) -> None:
    _prepare(context, fail=True)


@when("I relay one NATS outbox event")
def relay_event(context: Any) -> None:
    context.values["report"] = context.values["relay"].relay(
        context.values["connection"],
        TENANT,
        context.values["publisher"],
        limit=10,
    )


@then("the event is acknowledged with its message identity")
def event_acknowledged(context: Any) -> None:
    event = context.values["event"]
    client = context.values["client"]
    assert context.values["report"].published_event_ids == (event.event_id,)
    assert client.messages[0][0] == (f"poddown.outbox.v1.{TENANT}.episode.created")
    assert client.messages[0][2]["Nats-Msg-Id"] == str(event.event_id)
    assert json.loads(client.messages[0][1])["event_id"] == str(event.event_id)
    assert client.streams == [(NATS_OUTBOX_STREAM, ("poddown.outbox.v1.>",))]


@then("the failed NATS event remains pending with one attempt")
def failed_event_pending(context: Any) -> None:
    event = context.values["event"]
    stored = context.values["connection"].events[str(event.event_id)]
    assert context.values["report"].failed_event_ids == (event.event_id,)
    assert stored[-2] is None
    assert stored[-1] == 1


@given("a configured NATS outbox relay activity")
def configured_relay_activity(context: Any) -> None:
    connection = _Connection()
    event = _event()
    PostgresOutbox().append(connection, event)
    client = _Client()

    async def client_factory() -> _Client:
        return client

    context.values["connection"] = connection
    context.values["event"] = event
    context.values["client"] = client
    context.values["activity"] = build_nats_outbox_relay_activity(
        lambda: connection,
        client_factory,
    )


@when("the NATS outbox relay activity runs for one tenant")
def run_relay_activity(context: Any) -> None:
    context.values["result"] = asyncio.run(
        context.values["activity"](
            {"tenant_id": str(TENANT), "limit": 10},
        )
    )


@then("the relay activity returns the published event identity")
def relay_activity_returns_identity(context: Any) -> None:
    event = context.values["event"]
    assert context.values["result"] == {
        "published_event_ids": [str(event.event_id)],
        "failed_event_ids": [],
    }
    assert context.values["connection"].closed is True
    assert context.values["client"].closed is True


@given("a tenant-scoped outbox relay workflow input")
def relay_workflow_input(context: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def execute_activity(
        name: str,
        *,
        args: list[dict[str, object]],
        start_to_close_timeout: object,
        retry_policy: object,
        activity_id: str,
    ) -> dict[str, object]:
        calls.append(
            {
                "name": name,
                "args": args,
                "start_to_close_timeout": start_to_close_timeout,
                "retry_policy": retry_policy,
                "activity_id": activity_id,
            }
        )
        return {
            "published_event_ids": [str(uuid7())],
            "failed_event_ids": [],
        }

    monkeypatch.setattr(
        "poddown.nats_outbox.workflow.execute_activity",
        execute_activity,
    )
    context.values["payload"] = {"tenant_id": str(TENANT), "limit": 25}
    context.values["calls"] = calls


@when("the tenant relay workflow runs")
def run_relay_workflow(context: Any) -> None:
    context.values["workflow_result"] = asyncio.run(
        OutboxRelayWorkflow().run(context.values["payload"])
    )


@then("the workflow returns the relay report with bounded retry policy")
def relay_workflow_returns_report(context: Any) -> None:
    assert context.values["workflow_result"]["failed_event_ids"] == []
    call = context.values["calls"][0]
    assert call["name"] == OUTBOX_RELAY_ACTIVITY_NAME
    assert call["args"] == [{"tenant_id": str(TENANT), "limit": 25}]
    assert call["start_to_close_timeout"].total_seconds() == 300
    assert call["retry_policy"].maximum_attempts == 3
    assert call["activity_id"].startswith(f"{OUTBOX_RELAY_WORKFLOW_NAME}:{TENANT}:")
