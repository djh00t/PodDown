"""Tenant-scoped PostgreSQL persistence for immutable episode records."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from poddown.episode_service import (
    EpisodeNotFound,
    EpisodeRecord,
    EpisodeRepository,
    EpisodeState,
    IdempotencyConflict,
    StructuredFailure,
    VersionConflict,
)
from poddown.outbox import OutboxConnection, OutboxCursor
from poddown.persistence import PersistenceIntegrityError


class PostgresPersistenceCursor(OutboxCursor, Protocol):
    """Minimal DB-API cursor surface used by the episode repository."""

    rowcount: int

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> PostgresPersistenceCursor:
        """Execute one parameterized statement."""

    def fetchone(self) -> Sequence[object] | None:
        """Return one row from the last query."""

    def fetchall(self) -> list[Sequence[object]]:
        """Return all rows from the last query."""


class PostgresPersistenceConnection(OutboxConnection, Protocol):
    """Minimal transaction surface compatible with psycopg and test doubles."""

    def cursor(self) -> PostgresPersistenceCursor:
        """Return a cursor for the current transaction."""

    def transaction(self) -> AbstractContextManager[object]:
        """Return a context manager that commits or rolls back the transaction."""


ConnectionFactory = Callable[[], PostgresPersistenceConnection]

_EPISODE_COLUMNS = (
    "project_id, episode_id, idempotency_key, profile_name, source_sha256, "
    "source_content, source_byte_count, request_fingerprint, state, version, "
    "created_at, updated_at, qa_evidence, package_sha256, "
    "package_manifest_sha256, failure"
)


def _set_tenant(cursor: PostgresPersistenceCursor, tenant_id: UUID) -> None:
    """Bind RLS to the caller for this transaction before any protected query."""
    cursor.execute(
        "SELECT set_config('app.tenant_id', %s, true)",
        (str(tenant_id),),
    )


@contextmanager
def _tenant_cursor(
    connection_factory: ConnectionFactory,
    tenant_id: UUID,
) -> Iterator[PostgresPersistenceCursor]:
    """Open a transaction and set its tenant context before yielding a cursor."""
    connection = connection_factory()
    try:
        with connection.transaction():
            cursor = connection.cursor()
            _set_tenant(cursor, tenant_id)
            yield cursor
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def _json_mapping(value: object, field: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    parsed: object = value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"{field} is invalid") from error
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return cast(Mapping[str, object], parsed)


def _uuid7(value: object, field: str) -> UUID:
    parsed = value if isinstance(value, UUID) else UUID(str(value))
    if parsed.version != 7:
        raise ValueError(f"{field} must be a UUIDv7")
    return parsed


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} is invalid")
    return value


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} is invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _bytes(value: object, field: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise ValueError(f"{field} is invalid")


def _failure(value: object) -> StructuredFailure | None:
    parsed = _json_mapping(value, "failure")
    if parsed is None:
        return None
    code = parsed.get("code")
    stage = parsed.get("stage")
    message = parsed.get("message")
    retriable = parsed.get("retriable")
    details = parsed.get("details")
    status = parsed.get("status")
    if (
        not isinstance(code, str)
        or not isinstance(stage, str)
        or not isinstance(message, str)
    ):
        raise ValueError("failure is invalid")
    if type(retriable) is not bool or not isinstance(details, Mapping):
        raise ValueError("failure is invalid")
    if type(status) is not int:
        raise ValueError("failure is invalid")
    try:
        return StructuredFailure(
            code=code,
            stage=stage,
            message=message,
            retriable=retriable,
            details=cast(Mapping[str, object], details),
            status=status,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("failure is invalid") from error


def _record_from_row(tenant_id: UUID, row: Sequence[object]) -> EpisodeRecord:
    """Reconstruct a record and reject malformed or incomplete durable state."""
    if len(row) != 16:
        raise PersistenceIntegrityError(
            "persisted PostgreSQL episode row has an unexpected shape"
        )
    try:
        (
            project_id,
            episode_id,
            idempotency_key,
            profile_name,
            source_sha256,
            source_content,
            source_byte_count,
            request_fingerprint,
            state,
            version,
            created_at,
            updated_at,
            qa_evidence,
            package_sha256,
            package_manifest_sha256,
            failure,
        ) = row
        content = _bytes(source_content, "source_content")
        if type(source_byte_count) is not int:
            raise ValueError("source_byte_count is invalid")
        if type(version) is not int:
            raise ValueError("version is invalid")
        qa = _json_mapping(qa_evidence, "qa_evidence")
        return EpisodeRecord(
            tenant_id=tenant_id,
            project_id=_uuid7(project_id, "project_id"),
            episode_id=_uuid7(episode_id, "episode_id"),
            idempotency_key=_text(idempotency_key, "idempotency_key"),
            profile_name=_text(profile_name, "profile_name"),
            source_sha256=_text(source_sha256, "source_sha256"),
            source_bytes=source_byte_count,
            source_content=content,
            request_fingerprint=_text(request_fingerprint, "request_fingerprint"),
            state=EpisodeState(_text(state, "state")),
            version=version,
            created_at=_timestamp(created_at, "created_at"),
            updated_at=_timestamp(updated_at, "updated_at"),
            qa_evidence=qa,
            package_sha256=(
                _text(package_sha256, "package_sha256")
                if package_sha256 is not None
                else None
            ),
            package_manifest_sha256=(
                _text(package_manifest_sha256, "package_manifest_sha256")
                if package_manifest_sha256 is not None
                else None
            ),
            failure=_failure(failure),
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, PersistenceIntegrityError):
            raise
        raise PersistenceIntegrityError(
            "persisted PostgreSQL episode record is invalid"
        ) from error


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _json(value: object) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))


def _same_identity(current: EpisodeRecord, candidate: EpisodeRecord) -> bool:
    """Compare immutable fields while allowing a replay to carry a new ID."""
    return (
        current.tenant_id == candidate.tenant_id
        and current.project_id == candidate.project_id
        and current.idempotency_key == candidate.idempotency_key
        and current.profile_name == candidate.profile_name
        and current.source_sha256 == candidate.source_sha256
        and current.source_bytes == candidate.source_bytes
        and current.source_content == candidate.source_content
        and current.request_fingerprint == candidate.request_fingerprint
    )


class PostgresEpisodeRepository(EpisodeRepository):
    """Durable PostgreSQL adapter with explicit tenant context and replay rules."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def create(self, record: EpisodeRecord) -> EpisodeRecord:
        """Create or replay one source-bound episode atomically."""
        if not isinstance(record, EpisodeRecord):
            raise TypeError("record must be EpisodeRecord")
        with _tenant_cursor(self._connection_factory, record.tenant_id) as cursor:
            existing = self._by_key(cursor, record.tenant_id, record.idempotency_key)
            if existing is not None:
                if not _same_identity(existing, record):
                    raise IdempotencyConflict()
                return existing
            cursor.execute(
                """INSERT INTO episodes (
                    tenant_id, project_id, episode_id, idempotency_key,
                    profile_name, source_sha256, source_bytes, source_content,
                    source_byte_count, request_fingerprint, state, version,
                    created_at, updated_at, qa_evidence, package_sha256,
                    package_manifest_sha256, failure
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) ON CONFLICT (tenant_id, idempotency_key) DO NOTHING""",
                (
                    str(record.tenant_id),
                    str(record.project_id),
                    str(record.episode_id),
                    record.idempotency_key,
                    record.profile_name,
                    record.source_sha256,
                    record.source_content,
                    record.source_content,
                    record.source_bytes,
                    record.request_fingerprint,
                    record.state.value,
                    record.version,
                    record.created_at,
                    record.updated_at,
                    _json(record.qa_evidence)
                    if record.qa_evidence is not None
                    else None,
                    record.package_sha256,
                    record.package_manifest_sha256,
                    _json(record.failure.to_dict())
                    if record.failure is not None
                    else None,
                ),
            )
            persisted = self._by_key(cursor, record.tenant_id, record.idempotency_key)
            if persisted is None:
                raise IdempotencyConflict()
            if not _same_identity(persisted, record):
                raise IdempotencyConflict()
            return persisted

    def get(self, tenant_id: UUID, episode_id: UUID) -> EpisodeRecord:
        """Read an episode only within the caller's RLS and explicit scope."""
        _uuid7(tenant_id, "tenant_id")
        _uuid7(episode_id, "episode_id")
        with _tenant_cursor(self._connection_factory, tenant_id) as cursor:
            record = self._by_id(cursor, tenant_id, episode_id)
        if record is None:
            raise EpisodeNotFound()
        return record

    def replace(
        self,
        tenant_id: UUID,
        record: EpisodeRecord,
        *,
        expected_version: int,
    ) -> EpisodeRecord:
        """Replace mutable lifecycle fields using optimistic locking."""
        _uuid7(tenant_id, "tenant_id")
        if type(expected_version) is not int or expected_version < 1:
            raise VersionConflict()
        if record.tenant_id != tenant_id:
            raise EpisodeNotFound()
        with _tenant_cursor(self._connection_factory, tenant_id) as cursor:
            current = self._by_id(cursor, tenant_id, record.episode_id)
            if current is None:
                raise EpisodeNotFound()
            if (
                not _same_identity(current, record)
                or current.episode_id != record.episode_id
            ):
                raise ValueError("episode identity fields are immutable")
            if current.version != expected_version:
                raise VersionConflict()
            cursor.execute(
                """UPDATE episodes SET
                    state = %s, version = %s, updated_at = %s,
                    qa_evidence = %s, package_sha256 = %s,
                    package_manifest_sha256 = %s, failure = %s
                    WHERE version = %s AND tenant_id = %s AND episode_id = %s""",
                (
                    record.state.value,
                    record.version,
                    record.updated_at,
                    _json(record.qa_evidence)
                    if record.qa_evidence is not None
                    else None,
                    record.package_sha256,
                    record.package_manifest_sha256,
                    _json(record.failure.to_dict())
                    if record.failure is not None
                    else None,
                    expected_version,
                    str(tenant_id),
                    str(record.episode_id),
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict()
            persisted = self._by_id(cursor, tenant_id, record.episode_id)
        if persisted is None:
            raise PersistenceIntegrityError(
                "replaced PostgreSQL episode could not be reloaded"
            )
        return persisted

    @staticmethod
    def _by_key(
        cursor: PostgresPersistenceCursor,
        tenant_id: UUID,
        idempotency_key: str,
    ) -> EpisodeRecord | None:
        cursor.execute(
            f"SELECT {_EPISODE_COLUMNS} FROM episodes "
            "WHERE tenant_id = %s AND idempotency_key = %s",
            (str(tenant_id), idempotency_key),
        )
        row = cursor.fetchone()
        return _record_from_row(tenant_id, row) if row is not None else None

    @staticmethod
    def _by_id(
        cursor: PostgresPersistenceCursor,
        tenant_id: UUID,
        episode_id: UUID,
    ) -> EpisodeRecord | None:
        cursor.execute(
            f"SELECT {_EPISODE_COLUMNS} FROM episodes "
            "WHERE tenant_id = %s AND episode_id = %s",
            (str(tenant_id), str(episode_id)),
        )
        row = cursor.fetchone()
        return _record_from_row(tenant_id, row) if row is not None else None


__all__ = [
    "ConnectionFactory",
    "PostgresEpisodeRepository",
    "PostgresPersistenceConnection",
    "PostgresPersistenceCursor",
]
