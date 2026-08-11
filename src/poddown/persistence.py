"""Restart-safe local persistence and append-only usage metering adapters."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, cast
from uuid import UUID

from uuid6 import uuid7

from poddown.episode_service import (
    EpisodeNotFound,
    EpisodeRecord,
    EpisodeRepository,
    EpisodeState,
    IdempotencyConflict,
    StructuredFailure,
    VersionConflict,
)

if TYPE_CHECKING:
    from poddown.api.models import CommandReceipt

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_TEXT = re.compile(r"^\S[\s\S]{0,254}$")
_COMMANDS = frozenset({"create", "render", "publish"})
_DATABASE_INIT_LOCK = Lock()


class PersistenceError(ValueError):
    """Base error for durable local persistence failures."""


class PersistenceIntegrityError(PersistenceError):
    """Persisted state is malformed and cannot be safely reconstructed."""


class UsageConflict(PersistenceError):
    """A provider request was reused for different immutable usage evidence."""


class UsageNotFound(PersistenceError):
    """A usage event is absent from the caller's tenant scope."""


def _database_path(value: Path | str) -> Path:
    if not isinstance(value, (Path, str)) or str(value) == ":memory:":
        raise ValueError("a durable filesystem database path is required")
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _json_text(value: object) -> str:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("persisted timestamp is invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("persisted timestamp must be timezone-aware")
    return parsed


def _require_uuid7(value: object, field: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 7:
        raise ValueError(f"{field} must be a UUIDv7")
    return value


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or _TEXT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _require_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite non-negative Decimal")
    return value


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """Immutable usage and cost evidence for one provider request."""

    tenant_id: UUID
    project_id: UUID
    job_id: UUID
    provider_request_id: str
    operation: str
    units: Mapping[str, int]
    currency: str
    estimated_cost: Decimal
    reconciled_cost: Decimal | None
    created_at: datetime

    def __post_init__(self) -> None:
        _require_uuid7(self.tenant_id, "tenant_id")
        _require_uuid7(self.project_id, "project_id")
        _require_uuid7(self.job_id, "job_id")
        _require_text(self.provider_request_id, "provider_request_id")
        _require_text(self.operation, "operation")
        if not isinstance(self.units, Mapping) or not self.units:
            raise ValueError("units must be a non-empty mapping")
        normalized_units: dict[str, int] = {}
        for name, value in self.units.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("unit names must be non-empty strings")
            if type(value) is not int or value < 0:
                raise ValueError("unit values must be non-negative integers")
            normalized_units[name] = value
        if (
            not isinstance(self.currency, str)
            or _CURRENCY.fullmatch(self.currency) is None
        ):
            raise ValueError("currency must be an uppercase ISO-4217 code")
        _require_decimal(self.estimated_cost, "estimated_cost")
        if self.reconciled_cost is not None:
            _require_decimal(self.reconciled_cost, "reconciled_cost")
        _timestamp(self.created_at)
        object.__setattr__(self, "units", MappingProxyType(normalized_units))

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-shaped usage evidence."""
        return {
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "job_id": str(self.job_id),
            "provider_request_id": self.provider_request_id,
            "operation": self.operation,
            "units": dict(self.units),
            "currency": self.currency,
            "estimated_cost": format(self.estimated_cost, "f"),
            "reconciled_cost": (
                format(self.reconciled_cost, "f")
                if self.reconciled_cost is not None
                else None
            ),
            "created_at": _timestamp(self.created_at),
        }


class UsageLedger(Protocol):
    """Port for immutable tenant-scoped usage and cost evidence."""

    def record(self, event: UsageEvent) -> UsageEvent:
        """Append or replay one provider-request usage event."""

    def get(self, tenant_id: UUID, provider_request_id: str) -> UsageEvent:
        """Read one usage event within the caller's tenant scope."""

    def list_for_job(self, tenant_id: UUID, job_id: UUID) -> tuple[UsageEvent, ...]:
        """List usage events for one tenant-scoped job."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    episode_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_bytes INTEGER NOT NULL,
    request_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    qa_evidence TEXT,
    package_sha256 TEXT,
    failure TEXT,
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS episodes_tenant_project_idx
    ON episodes (tenant_id, project_id, episode_id);
"""

_COMMAND_RECEIPTS_SCHEMA = """
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
    UNIQUE (tenant_id, project_id, episode_id, command, idempotency_key)
);
"""

_SCHEMA += """
CREATE TABLE IF NOT EXISTS command_receipts (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, project_id, episode_id, command, idempotency_key)
);
CREATE TABLE IF NOT EXISTS usage_events (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    provider_request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    units TEXT NOT NULL,
    currency TEXT NOT NULL,
    estimated_cost TEXT NOT NULL,
    reconciled_cost TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, provider_request_id)
);
CREATE INDEX IF NOT EXISTS usage_events_job_idx
    ON usage_events (tenant_id, job_id, created_at, provider_request_id);
"""


def _command_receipt_unique_indexes(
    connection: sqlite3.Connection,
) -> set[tuple[str, ...]]:
    return {
        tuple(row[2] for row in connection.execute(f"PRAGMA index_info({index[1]})"))
        for index in connection.execute("PRAGMA index_list(command_receipts)")
        if index[2]
    }


def _migrate_command_receipts_schema(connection: sqlite3.Connection) -> None:
    legacy_identity = ("tenant_id", "idempotency_key")
    if legacy_identity not in _command_receipt_unique_indexes(connection):
        return
    connection.execute("ALTER TABLE command_receipts RENAME TO command_receipts_legacy")
    connection.executescript(_COMMAND_RECEIPTS_SCHEMA)
    connection.execute(
        """INSERT INTO command_receipts (
            tenant_id, project_id, episode_id, idempotency_key, command_id,
            command, accepted, state, created_at
        ) SELECT
            tenant_id, project_id, episode_id, idempotency_key, command_id,
            command, accepted, state, created_at
        FROM command_receipts_legacy"""
    )
    connection.execute("DROP TABLE command_receipts_legacy")


def _initialize_database(path: Path) -> None:
    with _DATABASE_INIT_LOCK:
        connection = sqlite3.connect(path, timeout=10.0)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(_SCHEMA)
            _migrate_command_receipts_schema(connection)
            connection.commit()
        finally:
            connection.close()


@contextmanager
def _read_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def _write_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _episode_values(record: EpisodeRecord) -> tuple[object, ...]:
    return (
        str(record.tenant_id),
        str(record.project_id),
        str(record.episode_id),
        record.idempotency_key,
        record.profile_name,
        record.source_sha256,
        record.source_bytes,
        record.request_fingerprint,
        record.state.value,
        record.version,
        _timestamp(record.created_at),
        _timestamp(record.updated_at),
        _json_text(record.qa_evidence) if record.qa_evidence is not None else None,
        record.package_sha256,
        _json_text(record.failure.to_dict()) if record.failure is not None else None,
    )


def _episode_from_row(row: sqlite3.Row) -> EpisodeRecord:
    try:
        qa_value = json.loads(row["qa_evidence"]) if row["qa_evidence"] else None
        if qa_value is not None and not isinstance(qa_value, Mapping):
            raise ValueError("qa evidence must be a mapping")
        failure_value = (
            json.loads(row["failure"]) if row["failure"] is not None else None
        )
        if row["failure"] is not None and not isinstance(failure_value, Mapping):
            raise ValueError("failure must be a mapping")
        failure = (
            StructuredFailure(
                code=failure_value["code"],
                stage=failure_value["stage"],
                message=failure_value["message"],
                retriable=failure_value["retriable"],
                details=failure_value["details"],
                status=failure_value["status"],
            )
            if isinstance(failure_value, Mapping)
            else None
        )
        return EpisodeRecord(
            tenant_id=UUID(row["tenant_id"]),
            project_id=UUID(row["project_id"]),
            episode_id=UUID(row["episode_id"]),
            idempotency_key=row["idempotency_key"],
            profile_name=row["profile_name"],
            source_sha256=row["source_sha256"],
            source_bytes=row["source_bytes"],
            request_fingerprint=row["request_fingerprint"],
            state=EpisodeState(row["state"]),
            version=row["version"],
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
            qa_evidence=qa_value,
            package_sha256=row["package_sha256"],
            failure=failure,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PersistenceIntegrityError(
            "persisted episode record is invalid"
        ) from error


def _same_episode_identity(current: EpisodeRecord, candidate: EpisodeRecord) -> bool:
    return (
        current.tenant_id == candidate.tenant_id
        and current.project_id == candidate.project_id
        and current.episode_id == candidate.episode_id
        and current.idempotency_key == candidate.idempotency_key
        and current.profile_name == candidate.profile_name
        and current.source_sha256 == candidate.source_sha256
        and current.source_bytes == candidate.source_bytes
        and current.request_fingerprint == candidate.request_fingerprint
    )


class SQLiteEpisodeRepository(EpisodeRepository):
    """Tenant-scoped SQLite implementation of the episode repository port."""

    def __init__(self, database: Path | str) -> None:
        self._database = _database_path(database)
        _initialize_database(self._database)

    def create(self, record: EpisodeRecord) -> EpisodeRecord:
        """Create or replay one record under a tenant-scoped idempotency key."""
        with _write_connection(self._database) as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE tenant_id = ? AND idempotency_key = ?",
                (str(record.tenant_id), record.idempotency_key),
            ).fetchone()
            if row is not None:
                existing = _episode_from_row(row)
                if existing.request_fingerprint != record.request_fingerprint:
                    raise IdempotencyConflict()
                return existing
            try:
                connection.execute(
                    """INSERT INTO episodes (
                        tenant_id, project_id, episode_id, idempotency_key,
                        profile_name, source_sha256, source_bytes,
                        request_fingerprint, state, version, created_at,
                        updated_at, qa_evidence, package_sha256, failure
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _episode_values(record),
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflict() from error
        return record

    def get(self, tenant_id: UUID, episode_id: UUID) -> EpisodeRecord:
        """Read one episode only within the caller's tenant scope."""
        _require_uuid7(tenant_id, "tenant_id")
        _require_uuid7(episode_id, "episode_id")
        with _read_connection(self._database) as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE tenant_id = ? AND episode_id = ?",
                (str(tenant_id), str(episode_id)),
            ).fetchone()
        if row is None:
            raise EpisodeNotFound()
        return _episode_from_row(row)

    def replace(
        self,
        tenant_id: UUID,
        record: EpisodeRecord,
        *,
        expected_version: int,
    ) -> EpisodeRecord:
        """Atomically replace one expected episode version."""
        _require_uuid7(tenant_id, "tenant_id")
        with _write_connection(self._database) as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE tenant_id = ? AND episode_id = ?",
                (str(tenant_id), str(record.episode_id)),
            ).fetchone()
            if row is None:
                raise EpisodeNotFound()
            current = _episode_from_row(row)
            if not _same_episode_identity(current, record):
                raise ValueError("episode identity fields are immutable")
            if current.version != expected_version:
                raise VersionConflict()
            result = connection.execute(
                """UPDATE episodes SET
                    state = ?, version = ?, updated_at = ?, qa_evidence = ?,
                    package_sha256 = ?, failure = ?
                    WHERE tenant_id = ? AND episode_id = ? AND version = ?""",
                (
                    record.state.value,
                    record.version,
                    _timestamp(record.updated_at),
                    _json_text(record.qa_evidence)
                    if record.qa_evidence is not None
                    else None,
                    record.package_sha256,
                    _json_text(record.failure.to_dict())
                    if record.failure is not None
                    else None,
                    str(tenant_id),
                    str(record.episode_id),
                    expected_version,
                ),
            )
            if result.rowcount != 1:
                raise VersionConflict()
        return record


def _receipt_from_row(row: sqlite3.Row) -> CommandReceipt:
    from poddown.api.models import CommandReceipt

    try:
        return CommandReceipt(
            command_id=UUID(row["command_id"]),
            episode_id=UUID(row["episode_id"]),
            idempotency_key=row["idempotency_key"],
            command=row["command"],
            accepted=bool(row["accepted"]),
            state=row["state"],
            created_at=_parse_timestamp(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PersistenceIntegrityError(
            "persisted command receipt is invalid"
        ) from error


class SQLiteCommandDispatcher:
    """Restart-safe command receipt adapter for the asynchronous API."""

    def __init__(
        self,
        database: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = _database_path(database)
        self._clock = clock or (lambda: datetime.now(UTC))
        _initialize_database(self._database)

    def submit(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: str,
        idempotency_key: str,
    ) -> CommandReceipt:
        """Persist or replay one tenant-scoped command receipt."""
        for field, value in (
            ("tenant_id", tenant_id),
            ("project_id", project_id),
            ("episode_id", episode_id),
        ):
            _require_uuid7(value, field)
        _require_text(command, "command")
        _require_text(idempotency_key, "idempotency_key")
        if command not in _COMMANDS:
            raise ValueError("command is invalid")
        command_value = cast(Literal["create", "render", "publish"], command)
        from poddown.api.models import CommandReceipt

        with _write_connection(self._database) as connection:
            row = connection.execute(
                "SELECT * FROM command_receipts "
                "WHERE tenant_id = ? AND project_id = ? AND episode_id = ? "
                "AND command = ? AND idempotency_key = ?",
                (
                    str(tenant_id),
                    str(project_id),
                    str(episode_id),
                    command_value,
                    idempotency_key,
                ),
            ).fetchone()
            if row is not None:
                return _receipt_from_row(row)
            rebound = connection.execute(
                "SELECT 1 FROM command_receipts "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (str(tenant_id), idempotency_key),
            ).fetchone()
            if rebound is not None:
                raise IdempotencyConflict()
            created_at = self._clock()
            receipt = CommandReceipt(
                command_id=uuid7(),
                episode_id=episode_id,
                idempotency_key=idempotency_key,
                command=command_value,
                created_at=created_at,
            )
            try:
                connection.execute(
                    """INSERT INTO command_receipts (
                        tenant_id, project_id, episode_id, idempotency_key,
                        command_id, command, accepted, state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        idempotency_key,
                        str(receipt.command_id),
                        receipt.command,
                        int(receipt.accepted),
                        receipt.state,
                        _timestamp(created_at),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflict() from error
        return receipt

    def replay(
        self,
        *,
        tenant_id: UUID,
        episode_id: UUID,
        command: Literal["create", "render", "publish"],
        idempotency_key: str,
    ) -> CommandReceipt | None:
        """Return an existing receipt without accepting a new command."""
        with _read_connection(self._database) as connection:
            row = connection.execute(
                """SELECT * FROM command_receipts
                   WHERE tenant_id = ? AND episode_id = ?
                   AND command = ? AND idempotency_key = ?""",
                (str(tenant_id), str(episode_id), command, idempotency_key),
            ).fetchone()
        return _receipt_from_row(row) if row is not None else None


def _usage_from_row(row: sqlite3.Row) -> UsageEvent:
    try:
        units = json.loads(row["units"])
        if not isinstance(units, Mapping):
            raise ValueError("units must be a mapping")
        reconciled = (
            Decimal(row["reconciled_cost"])
            if row["reconciled_cost"] is not None
            else None
        )
        return UsageEvent(
            tenant_id=UUID(row["tenant_id"]),
            project_id=UUID(row["project_id"]),
            job_id=UUID(row["job_id"]),
            provider_request_id=row["provider_request_id"],
            operation=row["operation"],
            units=units,
            currency=row["currency"],
            estimated_cost=Decimal(row["estimated_cost"]),
            reconciled_cost=reconciled,
            created_at=_parse_timestamp(row["created_at"]),
        )
    except (
        DecimalException,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise PersistenceIntegrityError("persisted usage event is invalid") from error


def _usage_values(event: UsageEvent) -> tuple[object, ...]:
    return (
        str(event.tenant_id),
        str(event.project_id),
        str(event.job_id),
        event.provider_request_id,
        event.operation,
        _json_text(event.units),
        event.currency,
        format(event.estimated_cost, "f"),
        format(event.reconciled_cost, "f")
        if event.reconciled_cost is not None
        else None,
        _timestamp(event.created_at),
    )


class SQLiteUsageLedger:
    """Append-only SQLite usage and provider-cost event ledger."""

    def __init__(self, database: Path | str) -> None:
        self._database = _database_path(database)
        _initialize_database(self._database)

    def record(self, event: UsageEvent) -> UsageEvent:
        """Append or replay one immutable provider-request event."""
        with _write_connection(self._database) as connection:
            row = connection.execute(
                """SELECT * FROM usage_events
                   WHERE tenant_id = ? AND provider_request_id = ?""",
                (str(event.tenant_id), event.provider_request_id),
            ).fetchone()
            if row is not None:
                existing = _usage_from_row(row)
                if existing != event:
                    raise UsageConflict()
                return existing
            try:
                connection.execute(
                    """INSERT INTO usage_events (
                        tenant_id, project_id, job_id, provider_request_id,
                        operation, units, currency, estimated_cost,
                        reconciled_cost, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _usage_values(event),
                )
            except sqlite3.IntegrityError as error:
                raise UsageConflict() from error
        return event

    def get(self, tenant_id: UUID, provider_request_id: str) -> UsageEvent:
        """Read one usage event within the caller's tenant scope."""
        _require_uuid7(tenant_id, "tenant_id")
        _require_text(provider_request_id, "provider_request_id")
        with _read_connection(self._database) as connection:
            row = connection.execute(
                """SELECT * FROM usage_events
                   WHERE tenant_id = ? AND provider_request_id = ?""",
                (str(tenant_id), provider_request_id),
            ).fetchone()
        if row is None:
            raise UsageNotFound()
        return _usage_from_row(row)

    def list_for_job(self, tenant_id: UUID, job_id: UUID) -> tuple[UsageEvent, ...]:
        """List usage events in stable creation/request order."""
        _require_uuid7(tenant_id, "tenant_id")
        _require_uuid7(job_id, "job_id")
        with _read_connection(self._database) as connection:
            rows = connection.execute(
                """SELECT * FROM usage_events
                   WHERE tenant_id = ? AND job_id = ?
                   ORDER BY created_at, provider_request_id""",
                (str(tenant_id), str(job_id)),
            ).fetchall()
        return tuple(_usage_from_row(row) for row in rows)


__all__ = [
    "PersistenceError",
    "PersistenceIntegrityError",
    "SQLiteCommandDispatcher",
    "SQLiteEpisodeRepository",
    "SQLiteUsageLedger",
    "UsageConflict",
    "UsageEvent",
    "UsageLedger",
    "UsageNotFound",
]
