"""BDD coverage for the transactional outbox boundary."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pytest_bdd import given, scenarios, then, when

from poddown.outbox import OutboxWriter

scenarios("../features/outbox.feature")

TENANT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")
EPISODE_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e4")
NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """CREATE TABLE episodes (id TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE outbox_events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            aggregate_version INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            publish_attempts INTEGER NOT NULL DEFAULT 0,
            published_at TEXT,
            created_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            claim_token TEXT,
            lease_until TEXT,
            UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version)
        );"""
    )
    return connection


@given("an open transaction with an episode mutation", target_fixture="state")
def open_transaction() -> dict[str, object]:
    connection = _connection()
    connection.execute("INSERT INTO episodes VALUES (?, ?)", (str(EPISODE_ID), "draft"))
    return {"connection": connection, "writer": OutboxWriter()}


@when("the mutation enqueues its episode event and the transaction rolls back")
def enqueue_then_rollback(state: dict[str, object]) -> None:
    writer = state["writer"]
    connection = state["connection"]
    assert isinstance(writer, OutboxWriter)
    assert isinstance(connection, sqlite3.Connection)
    writer.enqueue(
        connection,
        tenant_id=TENANT_ID,
        aggregate_type="episode",
        aggregate_id=EPISODE_ID,
        aggregate_version=1,
        event_type="episode.created",
        payload={"state": "draft"},
        created_at=NOW,
    )
    connection.rollback()


@then("neither the episode mutation nor the outbox event is durable")
def assert_rollback(state: dict[str, object]) -> None:
    connection = state["connection"]
    assert isinstance(connection, sqlite3.Connection)
    assert connection.execute("SELECT * FROM episodes").fetchone() is None
    assert connection.execute("SELECT * FROM outbox_events").fetchone() is None
    connection.close()


@given("an open transaction with a deterministic episode event", target_fixture="state")
def deterministic_event() -> dict[str, object]:
    return {"connection": _connection(), "writer": OutboxWriter()}


@when("the same event is enqueued twice")
def enqueue_twice(state: dict[str, object]) -> None:
    writer = state["writer"]
    connection = state["connection"]
    assert isinstance(writer, OutboxWriter)
    assert isinstance(connection, sqlite3.Connection)
    for _ in range(2):
        writer.enqueue(
            connection,
            tenant_id=TENANT_ID,
            aggregate_type="episode",
            aggregate_id=EPISODE_ID,
            aggregate_version=1,
            event_type="episode.created",
            payload={"state": "draft"},
            created_at=NOW,
        )


@then("one deterministic outbox event is retained without a commit")
def assert_replay(state: dict[str, object]) -> None:
    connection = state["connection"]
    assert isinstance(connection, sqlite3.Connection)
    rows = connection.execute("SELECT id FROM outbox_events").fetchall()
    assert rows == [("20c4f520-0854-76d9-abe4-66a3d35a2c09",)]
    connection.rollback()
    assert connection.execute("SELECT * FROM outbox_events").fetchone() is None
    connection.close()


@given("a committed deterministic episode event", target_fixture="lease_state")
def committed_event() -> dict[str, object]:
    connection = _connection()
    writer = OutboxWriter()
    writer.enqueue(
        connection,
        tenant_id=TENANT_ID,
        aggregate_type="episode",
        aggregate_id=EPISODE_ID,
        aggregate_version=1,
        event_type="episode.created",
        payload={"state": "draft"},
        created_at=NOW,
    )
    connection.commit()
    return {"connection": connection, "writer": writer}


@when("a worker claims the event and releases it for retry")
def claim_and_release(lease_state: dict[str, object]) -> None:
    connection = lease_state["connection"]
    writer = lease_state["writer"]
    assert isinstance(connection, sqlite3.Connection)
    assert isinstance(writer, OutboxWriter)
    claim = writer.claim_available(
        connection, limit=1, now=NOW, lease_for=timedelta(minutes=5)
    )[0]
    assert claim.claim_token is not None
    connection.commit()
    lease_state["claim"] = claim
    assert writer.release_for_retry(
        connection,
        event_id=claim.event_id,
        claim_token=claim.claim_token,
        now=NOW,
        retry_after=timedelta(minutes=2),
    )
    connection.commit()


@then("the event remains unavailable until the explicit retry delay ends")
def retry_delay_is_observed(lease_state: dict[str, object]) -> None:
    connection = lease_state["connection"]
    writer = lease_state["writer"]
    assert isinstance(connection, sqlite3.Connection)
    assert isinstance(writer, OutboxWriter)
    assert (
        writer.claim_available(
            connection,
            limit=1,
            now=NOW + timedelta(minutes=1),
            lease_for=timedelta(minutes=5),
        )
        == ()
    )
    assert (
        len(
            writer.claim_available(
                connection,
                limit=1,
                now=NOW + timedelta(minutes=2),
                lease_for=timedelta(minutes=5),
            )
        )
        == 1
    )
    connection.close()


@given("a worker claim whose lease has expired")
def expired_worker_claim(lease_state: dict[str, object]) -> None:
    """Create a claim and advance time past its lease deadline."""
    connection = lease_state["connection"]
    writer = lease_state["writer"]
    assert isinstance(connection, sqlite3.Connection)
    assert isinstance(writer, OutboxWriter)
    expired_claim = writer.claim_available(
        connection,
        limit=1,
        now=NOW,
        lease_for=timedelta(minutes=5),
    )[0]
    assert expired_claim.claim_token is not None
    lease_state["expired_claim"] = expired_claim


@when("the expired worker releases the event for retry")
def expired_worker_releases(lease_state: dict[str, object]) -> None:
    """Attempt the release using the expired worker's now-stale lease token."""
    connection = lease_state["connection"]
    writer = lease_state["writer"]
    expired_claim = lease_state["expired_claim"]
    assert isinstance(connection, sqlite3.Connection)
    assert isinstance(writer, OutboxWriter)
    assert hasattr(expired_claim, "event_id")
    assert hasattr(expired_claim, "claim_token")
    assert expired_claim.claim_token is not None
    lease_state["released"] = writer.release_for_retry(
        connection,
        event_id=expired_claim.event_id,
        claim_token=expired_claim.claim_token,
        now=NOW + timedelta(minutes=5),
        retry_after=timedelta(minutes=2),
    )


@then("the event retains its expired worker lease")
def event_retains_expired_lease(lease_state: dict[str, object]) -> None:
    """Confirm an expired worker cannot clear its own stale lease."""
    connection = lease_state["connection"]
    expired_claim = lease_state["expired_claim"]
    assert isinstance(connection, sqlite3.Connection)
    assert hasattr(expired_claim, "claim_token")
    assert lease_state["released"] is False
    assert connection.execute(
        "SELECT claim_token, lease_until FROM outbox_events"
    ).fetchone() == (
        str(expired_claim.claim_token),
        (NOW + timedelta(minutes=5)).isoformat(),
    )
    connection.close()
