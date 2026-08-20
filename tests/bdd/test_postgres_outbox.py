"""BDD bindings for the tenant-scoped transactional outbox."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when
from uuid6 import uuid7

from poddown.outbox import OutboxConflict, OutboxEvent, PostgresOutbox

scenarios("../features/postgres_outbox.feature")


TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
AGGREGATE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
CREATED = datetime(2026, 8, 14, tzinfo=UTC)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.last_row: tuple[Any, ...] | None = None
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount = -1

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.rowcount = 0
        if normalized.startswith("select event_id"):
            event_id = str(params[1])
            event = self.connection.events.get(event_id)
            self.last_row = self.connection.row(event) if event is not None else None
            self.rows = [self.last_row] if self.last_row is not None else []
        elif normalized.startswith("insert into outbox_events"):
            event_id = str(params[2])
            if event_id not in self.connection.events:
                self.connection.events[event_id] = (
                    *params[:8],
                    None,
                    params[8],
                )
                self.rowcount = 1
        elif normalized.startswith("update outbox_events"):
            event_id = str(params[-1])
            event = self.connection.events.get(event_id)
            if event is not None and event[-2] is None:
                values = list(event)
                values[-2] = params[0]
                values[-1] = event[-1]
                self.connection.events[event_id] = tuple(values)
                self.rowcount = 1
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.last_row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.events: dict[str, tuple[object, ...]] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self

    @staticmethod
    def row(values: tuple[object, ...]) -> tuple[object, ...]:
        (
            tenant_id,
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


def _event(*, payload: dict[str, object] | None = None) -> OutboxEvent:
    return OutboxEvent(
        tenant_id=TENANT,
        project_id=PROJECT,
        event_id=uuid7(),
        aggregate_type="episode",
        aggregate_id=AGGREGATE,
        event_type="episode.created",
        payload={"source_sha256": "a" * 64} if payload is None else payload,
        created_at=CREATED,
    )


@given("a recording PostgreSQL outbox connection")
def recording_connection(context) -> None:
    context.values["connection"] = _Connection()
    context.values["outbox"] = PostgresOutbox()
    context.values["event"] = _event()


@when("I append the same outbox event twice")
def append_same_event(context) -> None:
    connection = context.values["connection"]
    event = context.values["event"]
    context.values["first"] = context.values["outbox"].append(connection, event)
    context.values["second"] = context.values["outbox"].append(connection, event)


@then("the event is inserted once and both calls return the same event")
def identical_replay(context) -> None:
    assert context.values["first"] == context.values["event"]
    assert context.values["second"] == context.values["event"]
    inserts = [
        statement
        for statement in context.values["connection"].statements
        if statement[0].lstrip().casefold().startswith("insert into outbox_events")
    ]
    assert len(inserts) == 1


@when("I append an event identity with different payload")
def append_conflicting_event(context) -> None:
    event = context.values["event"]
    context.values["outbox"].append(context.values["connection"], event)
    with pytest.raises(OutboxConflict):
        context.values["outbox"].append(
            context.values["connection"],
            OutboxEvent(
                tenant_id=event.tenant_id,
                project_id=event.project_id,
                event_id=event.event_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload={"source_sha256": "b" * 64},
                created_at=event.created_at,
            ),
        )


@then("the outbox conflict is rejected")
def conflict_rejected(context) -> None:
    assert context.values["connection"].events


@when("I mark an outbox event published twice")
def publish_twice(context) -> None:
    connection = context.values["connection"]
    event = context.values["event"]
    context.values["outbox"].append(connection, event)
    first_time = datetime(2026, 8, 14, 1, tzinfo=UTC)
    second_time = datetime(2026, 8, 14, 2, tzinfo=UTC)
    context.values["first"] = context.values["outbox"].mark_published(
        connection, event.tenant_id, event.event_id, first_time
    )
    context.values["second"] = context.values["outbox"].mark_published(
        connection, event.tenant_id, event.event_id, second_time
    )


@then("the event has one immutable publication timestamp")
def publication_is_immutable(context) -> None:
    assert context.values["first"].published_at == datetime(2026, 8, 14, 1, tzinfo=UTC)
    assert context.values["second"].published_at == context.values["first"].published_at


@when("I append an already published event")
def append_published_event(context) -> None:
    connection = context.values["connection"]
    event = context.values["event"]
    context.values["outbox"].append(connection, event)
    context.values["published"] = context.values["outbox"].mark_published(
        connection,
        event.tenant_id,
        event.event_id,
        datetime(2026, 8, 14, 3, tzinfo=UTC),
    )
    context.values["replay"] = context.values["outbox"].append(connection, event)


@then("the replay retains the publication timestamp")
def replay_retains_publication_timestamp(context) -> None:
    assert (
        context.values["replay"].published_at
        == context.values["published"].published_at
    )
