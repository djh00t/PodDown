"""SQLite integration contracts for restart-safe episode persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from poddown.api import create_app
from poddown.episode_service import (
    EpisodeNotFound,
    EpisodeRecord,
    EpisodeState,
    IdempotencyConflict,
    VersionConflict,
)
from poddown.persistence import (
    PersistenceIntegrityError,
    SQLiteCommandDispatcher,
    SQLiteEpisodeRepository,
    SQLiteUsageLedger,
    UsageConflict,
    UsageEvent,
    UsageNotFound,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
OTHER_TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
OTHER_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
EPISODE_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")
JOB_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b15")
CREATED_AT = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
SOURCE_CONTENT = b"source-bound durable persistence fixture".ljust(48, b"!")


def episode_record(
    *,
    tenant_id: UUID = TENANT_ID,
    project_id: UUID = PROJECT_ID,
    episode_id: UUID = EPISODE_ID,
    idempotency_key: str = "create-1",
    request_fingerprint: str = "a" * 64,
    state: EpisodeState = EpisodeState.VALIDATED,
    version: int = 1,
) -> EpisodeRecord:
    return EpisodeRecord(
        tenant_id=tenant_id,
        project_id=project_id,
        episode_id=episode_id,
        idempotency_key=idempotency_key,
        profile_name="technical-dialogue",
        source_sha256=hashlib.sha256(SOURCE_CONTENT).hexdigest(),
        source_bytes=48,
        source_content=SOURCE_CONTENT,
        request_fingerprint=request_fingerprint,
        state=state,
        version=version,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


def usage_event(
    *,
    tenant_id: UUID = TENANT_ID,
    provider_request_id: str = "provider-request-1",
    estimated_cost: Decimal = Decimal("0.0040"),
) -> UsageEvent:
    return UsageEvent(
        tenant_id=tenant_id,
        project_id=PROJECT_ID,
        job_id=JOB_ID,
        provider_request_id=provider_request_id,
        operation="render",
        units={"input_tokens": 12, "output_bytes": 48},
        currency="USD",
        estimated_cost=estimated_cost,
        reconciled_cost=Decimal("0.0035"),
        created_at=CREATED_AT,
    )


def test_episode_survives_restart_and_hides_other_tenants(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    first = SQLiteEpisodeRepository(database)
    record = first.create(episode_record())

    second = SQLiteEpisodeRepository(database)
    assert second.get(TENANT_ID, EPISODE_ID) == record
    with pytest.raises(EpisodeNotFound):
        second.get(OTHER_TENANT_ID, EPISODE_ID)
    with pytest.raises(EpisodeNotFound):
        second.get(TENANT_ID, UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b16"))


def test_package_manifest_digest_survives_restart_separately_from_package_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "poddown.sqlite3"
    record = replace(
        episode_record(state=EpisodeState.PACKAGED),
        package_sha256="a" * 64,
        package_manifest_sha256="b" * 64,
    )

    SQLiteEpisodeRepository(database).create(record)

    restored = SQLiteEpisodeRepository(database).get(TENANT_ID, EPISODE_ID)
    assert restored.package_sha256 == "a" * 64
    assert restored.package_manifest_sha256 == "b" * 64


def test_database_initializes_wal_mode(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    SQLiteEpisodeRepository(database)
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        connection.close()


def test_episode_idempotency_replays_and_conflicts(tmp_path: Path) -> None:
    repository = SQLiteEpisodeRepository(tmp_path / "poddown.sqlite3")
    record = episode_record()

    assert repository.create(record) == record
    assert repository.create(record) == record
    with pytest.raises(IdempotencyConflict):
        repository.create(
            episode_record(request_fingerprint="c" * 64, episode_id=JOB_ID)
        )


def test_concurrent_episode_creation_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    repository = SQLiteEpisodeRepository(database)
    record = episode_record()

    def create() -> EpisodeRecord:
        return repository.create(record)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: create(), range(4)))

    assert results == [record] * 4
    assert SQLiteEpisodeRepository(database).get(TENANT_ID, EPISODE_ID) == record


def test_stale_version_update_fails_atomically(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    repository = SQLiteEpisodeRepository(database)
    record = repository.create(episode_record())
    updated = episode_record(state=EpisodeState.SCRIPTED, version=2)

    assert (
        repository.replace(
            TENANT_ID,
            updated,
            expected_version=record.version,
        )
        == updated
    )
    with pytest.raises(VersionConflict):
        repository.replace(TENANT_ID, updated, expected_version=record.version)
    assert repository.get(TENANT_ID, EPISODE_ID) == updated


def test_command_receipt_survives_restart_and_conflicts(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    first = SQLiteCommandDispatcher(database)
    receipt = first.submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        command="render",
        idempotency_key="render-1",
    )

    second = SQLiteCommandDispatcher(database)
    assert (
        second.submit(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            command="render",
            idempotency_key="render-1",
        )
        == receipt
    )
    with pytest.raises(IdempotencyConflict):
        second.submit(
            tenant_id=TENANT_ID,
            project_id=OTHER_PROJECT_ID,
            episode_id=EPISODE_ID,
            command="publish",
            idempotency_key="render-1",
        )


def test_command_receipt_schema_uniquely_identifies_the_full_command(
    tmp_path: Path,
) -> None:
    database = tmp_path / "poddown.sqlite3"
    SQLiteCommandDispatcher(database)
    connection = sqlite3.connect(database)
    try:
        unique_indexes = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info({index[1]})")
            )
            for index in connection.execute("PRAGMA index_list(command_receipts)")
            if index[2]
        }
    finally:
        connection.close()

    assert (
        "tenant_id",
        "project_id",
        "episode_id",
        "command",
        "idempotency_key",
    ) in unique_indexes
    assert ("tenant_id", "idempotency_key") not in unique_indexes


def test_legacy_command_receipt_table_migrates_without_losing_a_replay(
    tmp_path: Path,
) -> None:
    database = tmp_path / "poddown.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE command_receipts (
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                command_id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (tenant_id, idempotency_key)
            );
            """
        )
        connection.execute(
            """INSERT INTO command_receipts (
                tenant_id, project_id, episode_id, idempotency_key,
                command_id, command, accepted, state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(TENANT_ID),
                str(PROJECT_ID),
                str(EPISODE_ID),
                "render-1",
                str(JOB_ID),
                "render",
                1,
                "queued",
                CREATED_AT.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    receipt = SQLiteCommandDispatcher(database).submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        command="render",
        idempotency_key="render-1",
    )

    assert receipt.command_id == JOB_ID
    connection = sqlite3.connect(database)
    try:
        unique_indexes = {
            tuple(
                column[2]
                for column in connection.execute(f"PRAGMA index_info({index[1]})")
            )
            for index in connection.execute("PRAGMA index_list(command_receipts)")
            if index[2]
        }
    finally:
        connection.close()
    assert (
        "tenant_id",
        "project_id",
        "episode_id",
        "command",
        "idempotency_key",
    ) in unique_indexes


def test_concurrent_command_receipt_creation_replays_one_stable_receipt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "poddown.sqlite3"

    def submit():
        return SQLiteCommandDispatcher(database).submit(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            command="render",
            idempotency_key="render-1",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        receipts = tuple(executor.map(lambda _: submit(), range(4)))

    assert receipts == (receipts[0],) * 4


def test_usage_ledger_is_immutable_restart_safe_and_tenant_scoped(
    tmp_path: Path,
) -> None:
    database = tmp_path / "poddown.sqlite3"
    first = SQLiteUsageLedger(database)
    event = first.record(usage_event())

    second = SQLiteUsageLedger(database)
    assert second.record(event) == event
    assert second.get(TENANT_ID, event.provider_request_id) == event
    with pytest.raises(UsageConflict):
        second.record(usage_event(estimated_cost=Decimal("0.0050")))
    with pytest.raises(UsageNotFound):
        second.get(OTHER_TENANT_ID, event.provider_request_id)
    assert second.list_for_job(TENANT_ID, JOB_ID) == (event,)


def test_malformed_persisted_episode_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    SQLiteEpisodeRepository(database).create(episode_record())
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE episodes SET state = ? WHERE episode_id = ?",
            ("not-a-state", str(EPISODE_ID)),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceIntegrityError):
        SQLiteEpisodeRepository(database).get(TENANT_ID, EPISODE_ID)


@pytest.mark.parametrize("failure", ["null", "[]", "42", "false"])
def test_non_object_persisted_failure_fails_closed(
    tmp_path: Path, failure: str
) -> None:
    database = tmp_path / "poddown.sqlite3"
    SQLiteEpisodeRepository(database).create(episode_record())
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE episodes SET failure = ?", (failure,))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceIntegrityError):
        SQLiteEpisodeRepository(database).get(TENANT_ID, EPISODE_ID)


def test_usage_events_order_by_normalized_utc_timestamp(tmp_path: Path) -> None:
    ledger = SQLiteUsageLedger(tmp_path / "poddown.sqlite3")
    later_textually = replace(
        usage_event(provider_request_id="offset-later"),
        created_at=datetime(2026, 8, 10, 1, 0, tzinfo=timezone(timedelta(hours=5))),
    )
    earlier_textually = replace(
        usage_event(provider_request_id="utc-earlier"),
        created_at=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
    )
    ledger.record(later_textually)
    ledger.record(earlier_textually)

    assert [
        event.provider_request_id for event in ledger.list_for_job(TENANT_ID, JOB_ID)
    ] == [
        "offset-later",
        "utc-earlier",
    ]


def test_malformed_persisted_usage_cost_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    event = SQLiteUsageLedger(database).record(usage_event())
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE usage_events SET estimated_cost = ? "
            "WHERE tenant_id = ? AND provider_request_id = ?",
            ("not-a-decimal", str(TENANT_ID), event.provider_request_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceIntegrityError):
        SQLiteUsageLedger(database).get(TENANT_ID, event.provider_request_id)


def test_api_persistence_mode_survives_app_restart(tmp_path: Path) -> None:
    database = tmp_path / "poddown.sqlite3"
    headers = {
        "X-Tenant-ID": str(TENANT_ID),
        "X-Project-ID": str(PROJECT_ID),
        "Idempotency-Key": "create-api-1",
    }
    payload = {
        "source": "---\npoddown:\n  profile: default\n---\n# Demo\n\nHello.\n",
        "profile": "default",
    }

    from fastapi.testclient import TestClient

    first = TestClient(create_app(database_path=database))
    created = first.post("/v1/episodes", headers=headers, json=payload)
    assert created.status_code == 202
    episode_id = created.json()["episode"]["id"]
    command_headers = {
        **headers,
        "Idempotency-Key": "render-api-1",
    }
    receipt = first.post(
        f"/v1/episodes/{episode_id}/render",
        headers=command_headers,
    )
    assert receipt.status_code == 202

    second = TestClient(create_app(database_path=database))
    restored = second.get(
        f"/v1/episodes/{episode_id}",
        headers={**headers, "Idempotency-Key": "read-only"},
    )
    replayed = second.post(
        f"/v1/episodes/{episode_id}/render",
        headers=command_headers,
    )
    assert restored.status_code == 200
    assert restored.json()["id"] == episode_id
    assert replayed.status_code == 202
    assert replayed.json()["command_id"] == receipt.json()["command_id"]
