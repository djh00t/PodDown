"""BDD bindings for tenant-scoped PostgreSQL episode persistence."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.episode_service import EpisodeRecord, EpisodeState, VersionConflict
from poddown.postgres_persistence import PostgresEpisodeRepository

scenarios("../features/postgres_episode_persistence.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
EPISODE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")
CREATED = datetime(2026, 8, 14, tzinfo=UTC)
SOURCE = b"postgres source bytes must be immutable"


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.rowcount = 0
        self._row: tuple[object, ...] | None = None

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split()).casefold()
        self.connection.statements.append((sql, params))
        self.rowcount = 0
        self._row = None
        if normalized.startswith("select set_config"):
            return self
        if normalized.startswith("select project_id"):
            if "where tenant_id = %s and idempotency_key" in normalized:
                record = self.connection.by_key.get((str(params[0]), str(params[1])))
            else:
                record = self.connection.rows.get((str(params[0]), str(params[1])))
            if record is not None:
                self._row = self.connection.row(record)
            return self
        if normalized.startswith("insert into episodes"):
            record = EpisodeRecord(
                tenant_id=UUID(str(params[0])),
                project_id=UUID(str(params[1])),
                episode_id=UUID(str(params[2])),
                idempotency_key=str(params[3]),
                profile_name=str(params[4]),
                source_sha256=str(params[5]),
                source_content=bytes(params[7]),
                source_bytes=int(params[8]),
                request_fingerprint=str(params[9]),
                state=EpisodeState(str(params[10])),
                version=int(params[11]),
                created_at=params[12],
                updated_at=params[13],
            )
            key = (str(record.tenant_id), str(record.episode_id))
            if key not in self.connection.rows:
                self.connection.rows[key] = record
                self.connection.by_key[
                    (str(record.tenant_id), record.idempotency_key)
                ] = record
                self.rowcount = 1
            return self
        if normalized.startswith("update episodes"):
            key = (str(params[-2]), str(params[-1]))
            record = self.connection.rows[key]
            if record.version == int(params[-3]):
                self.connection.rows[key] = replace(
                    record,
                    state=EpisodeState(str(params[0])),
                    version=int(params[1]),
                    updated_at=params[2],
                )
                self.rowcount = 1
            return self
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], EpisodeRecord] = {}
        self.by_key: dict[tuple[str, str], EpisodeRecord] = {}
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield self

    @staticmethod
    def row(record: EpisodeRecord) -> tuple[object, ...]:
        return (
            record.project_id,
            record.episode_id,
            record.idempotency_key,
            record.profile_name,
            record.source_sha256,
            record.source_content,
            record.source_bytes,
            record.request_fingerprint,
            record.state.value,
            record.version,
            record.created_at,
            record.updated_at,
            None,
            record.package_sha256,
            record.package_manifest_sha256,
            None,
        )


def _episode() -> EpisodeRecord:
    return EpisodeRecord(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        idempotency_key="create-1",
        profile_name="technical-dialogue",
        source_sha256=hashlib.sha256(SOURCE).hexdigest(),
        source_bytes=len(SOURCE),
        source_content=SOURCE,
        request_fingerprint="a" * 64,
        state=EpisodeState.VALIDATED,
        version=1,
        created_at=CREATED,
        updated_at=CREATED,
    )


@given("a recording PostgreSQL episode repository")
def recording_repository(context: Any) -> None:
    connection = _Connection()
    context.values["connection"] = connection
    context.values["repository"] = PostgresEpisodeRepository(lambda: connection)
    context.values["episode"] = _episode()


@when("I create and replay a PostgreSQL episode")
def create_and_replay(context: Any) -> None:
    repository = context.values["repository"]
    episode = context.values["episode"]
    repository.create(episode)
    context.values["replay"] = repository.create(episode)


@then("the exact source bytes and identity are preserved")
def source_bytes_preserved(context: Any) -> None:
    replay = context.values["replay"]
    assert replay.source_content == SOURCE
    assert replay.source_sha256 == hashlib.sha256(SOURCE).hexdigest()


@when("I replace the same PostgreSQL episode twice with one expected version")
def replace_twice(context: Any) -> None:
    repository = context.values["repository"]
    episode = context.values["episode"]
    repository.create(episode)
    updated = replace(
        episode,
        state=EpisodeState.SCRIPTED,
        version=2,
        updated_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
    )
    repository.replace(TENANT, updated, expected_version=1)
    with pytest.raises(VersionConflict):
        repository.replace(TENANT, updated, expected_version=1)


@then("the stale PostgreSQL replacement is rejected")
def stale_replacement_rejected(context: Any) -> None:
    assert context.values["repository"]
