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
from poddown.providers.contracts import ProviderEvidence

if TYPE_CHECKING:
    from poddown.api.models import CommandReceipt
    from poddown.audio.production_workflow import PackageCompletion

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

    def record_evidence(
        self, evidence: ProviderEvidence, event: UsageEvent
    ) -> tuple[ProviderEvidence, UsageEvent]:
        """Atomically append or replay normalized provider evidence and usage."""

    def get_evidence(
        self, tenant_id: UUID, provider_request_id: str
    ) -> ProviderEvidence:
        """Read normalized provider evidence within the caller's tenant scope."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    episode_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_bytes INTEGER NOT NULL,
    source_content BLOB,
    request_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    qa_evidence TEXT,
    package_sha256 TEXT,
    package_manifest_sha256 TEXT,
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
    workflow_id TEXT,
    command_payload TEXT NOT NULL DEFAULT '{}',
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
    workflow_id TEXT,
    command_payload TEXT NOT NULL DEFAULT '{}',
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
CREATE TABLE IF NOT EXISTS provider_evidence (
    tenant_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    usage TEXT NOT NULL,
    currency TEXT NOT NULL,
    estimated_cost TEXT NOT NULL,
    reconciled_cost TEXT,
    latency_ms INTEGER NOT NULL,
    retry_count INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    PRIMARY KEY (tenant_id, request_id)
);
CREATE TABLE IF NOT EXISTS temporal_command_receipts (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    state TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    command_payload TEXT NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS temporal_command_receipts_identity_idx
    ON temporal_command_receipts (
        tenant_id, project_id, episode_id, command, idempotency_key
    );
CREATE TABLE IF NOT EXISTS package_completions (
    episode_id TEXT NOT NULL,
    episode_version_id TEXT NOT NULL,
    completion_json TEXT NOT NULL,
    PRIMARY KEY (episode_id, episode_version_id)
);
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


def _migrate_command_payload_column(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(command_receipts)")
    }
    if "command_payload" not in columns:
        connection.execute(
            "ALTER TABLE command_receipts ADD COLUMN command_payload "
            "TEXT NOT NULL DEFAULT '{}'"
        )


def _migrate_command_workflow_column(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(command_receipts)")
    }
    if "workflow_id" not in columns:
        connection.execute("ALTER TABLE command_receipts ADD COLUMN workflow_id TEXT")


def _migrate_episode_manifest_column(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(episodes)")}
    if "package_manifest_sha256" not in columns:
        connection.execute(
            "ALTER TABLE episodes ADD COLUMN package_manifest_sha256 TEXT"
        )


def _migrate_episode_source_content_column(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(episodes)")}
    if "source_content" not in columns:
        connection.execute("ALTER TABLE episodes ADD COLUMN source_content BLOB")


def _initialize_database(path: Path) -> None:
    with _DATABASE_INIT_LOCK:
        connection = sqlite3.connect(path, timeout=10.0)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(_SCHEMA)
            _migrate_episode_manifest_column(connection)
            _migrate_episode_source_content_column(connection)
            _migrate_command_receipts_schema(connection)
            _migrate_command_payload_column(connection)
            _migrate_command_workflow_column(connection)
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
        record.source_content,
        record.request_fingerprint,
        record.state.value,
        record.version,
        _timestamp(record.created_at),
        _timestamp(record.updated_at),
        _json_text(record.qa_evidence) if record.qa_evidence is not None else None,
        record.package_sha256,
        record.package_manifest_sha256,
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
        source_content = row["source_content"]
        if not isinstance(source_content, (bytes, bytearray)):
            raise ValueError("source content is missing")
        return EpisodeRecord(
            tenant_id=UUID(row["tenant_id"]),
            project_id=UUID(row["project_id"]),
            episode_id=UUID(row["episode_id"]),
            idempotency_key=row["idempotency_key"],
            profile_name=row["profile_name"],
            source_sha256=row["source_sha256"],
            source_bytes=row["source_bytes"],
            source_content=bytes(source_content),
            request_fingerprint=row["request_fingerprint"],
            state=EpisodeState(row["state"]),
            version=row["version"],
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
            qa_evidence=qa_value,
            package_sha256=row["package_sha256"],
            package_manifest_sha256=row["package_manifest_sha256"],
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
        and current.source_content == candidate.source_content
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
                        source_content, request_fingerprint, state, version, created_at,
                        updated_at, qa_evidence, package_sha256,
                        package_manifest_sha256, failure
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    package_sha256 = ?, package_manifest_sha256 = ?, failure = ?
                    WHERE tenant_id = ? AND episode_id = ? AND version = ?""",
                (
                    record.state.value,
                    record.version,
                    _timestamp(record.updated_at),
                    _json_text(record.qa_evidence)
                    if record.qa_evidence is not None
                    else None,
                    record.package_sha256,
                    record.package_manifest_sha256,
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
            workflow_id=row["workflow_id"],
            created_at=_parse_timestamp(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PersistenceIntegrityError(
            "persisted command receipt is invalid"
        ) from error


def _temporal_receipt_from_row(row: sqlite3.Row) -> CommandReceipt:
    """Reconstruct one durable Temporal command receipt."""
    from poddown.api.models import CommandReceipt

    try:
        return CommandReceipt(
            command_id=UUID(row["command_id"]),
            episode_id=UUID(row["episode_id"]),
            idempotency_key=row["idempotency_key"],
            command=row["command"],
            accepted=bool(row["accepted"]),
            state=row["state"],
            workflow_id=row["workflow_id"],
            created_at=_parse_timestamp(row["created_at"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PersistenceIntegrityError(
            "persisted Temporal command receipt is invalid"
        ) from error


class SQLiteTemporalCommandReceiptStore:
    """Restart-safe receipt store for deterministic Temporal command dispatch."""

    def __init__(self, database: Path | str) -> None:
        self._database = _database_path(database)
        _initialize_database(self._database)

    def reserve(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: tuple[UUID, UUID, Literal["create", "render", "publish"]],
        receipt: CommandReceipt,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Persist one queued Temporal receipt or return its exact replay."""
        project_id, episode_id, command = identity
        if receipt.workflow_id is None:
            raise ValueError("Temporal receipt workflow identity is required")
        if receipt.episode_id != episode_id or receipt.command != command:
            raise IdempotencyConflict()
        payload_text = _json_text(payload or {})
        with _write_connection(self._database) as connection:
            row = connection.execute(
                """SELECT * FROM temporal_command_receipts
                   WHERE tenant_id = ? AND idempotency_key = ?""",
                (str(tenant_id), idempotency_key),
            ).fetchone()
            if row is not None:
                stored_receipt = _temporal_receipt_from_row(row)
                if (
                    row["project_id"],
                    row["episode_id"],
                    row["command"],
                ) != (str(project_id), str(episode_id), command):
                    raise IdempotencyConflict()
                if stored_receipt.workflow_id != receipt.workflow_id:
                    raise IdempotencyConflict()
                if row["command_payload"] != payload_text:
                    raise IdempotencyConflict()
                return stored_receipt
            try:
                connection.execute(
                    """INSERT INTO temporal_command_receipts (
                        tenant_id, project_id, episode_id, idempotency_key,
                        command_id, command, accepted, state, workflow_id,
                        created_at, updated_at, command_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        idempotency_key,
                        str(receipt.command_id),
                        receipt.command,
                        int(receipt.accepted),
                        receipt.state,
                        receipt.workflow_id,
                        _timestamp(receipt.created_at or datetime.now(UTC)),
                        None,
                        payload_text,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflict() from error
        return receipt

    def reconcile_dispatched(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: tuple[UUID, UUID, Literal["create", "render", "publish"]],
        receipt: CommandReceipt,
    ) -> CommandReceipt:
        """Advance the matching queued receipt after Temporal accepts it."""
        project_id, episode_id, command = identity
        with _write_connection(self._database) as connection:
            row = connection.execute(
                """SELECT * FROM temporal_command_receipts
                   WHERE tenant_id = ? AND idempotency_key = ?""",
                (str(tenant_id), idempotency_key),
            ).fetchone()
            if row is None:
                raise IdempotencyConflict()
            stored_receipt = _temporal_receipt_from_row(row)
            if (
                row["project_id"],
                row["episode_id"],
                row["command"],
            ) != (str(project_id), str(episode_id), command):
                raise IdempotencyConflict()
            if (
                stored_receipt.command_id != receipt.command_id
                or stored_receipt.workflow_id != receipt.workflow_id
            ):
                raise IdempotencyConflict()
            if stored_receipt.state == "dispatched":
                return stored_receipt
            if stored_receipt.state != "queued" or receipt.state != "dispatched":
                raise IdempotencyConflict()
            connection.execute(
                """UPDATE temporal_command_receipts
                   SET state = ?, updated_at = ?
                   WHERE tenant_id = ? AND idempotency_key = ?""",
                (
                    receipt.state,
                    _timestamp(datetime.now(UTC)),
                    str(tenant_id),
                    idempotency_key,
                ),
            )
        return receipt

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID | None = None,
        episode_id: UUID,
        command: Literal["create", "render", "publish"],
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt | None:
        """Return a matching Temporal receipt without starting a workflow."""
        with _read_connection(self._database) as connection:
            if project_id is None:
                row = connection.execute(
                    """SELECT * FROM temporal_command_receipts
                       WHERE tenant_id = ? AND episode_id = ?
                       AND command = ? AND idempotency_key = ?""",
                    (str(tenant_id), str(episode_id), command, idempotency_key),
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT * FROM temporal_command_receipts
                       WHERE tenant_id = ? AND project_id = ? AND episode_id = ?
                       AND command = ? AND idempotency_key = ?""",
                    (
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        command,
                        idempotency_key,
                    ),
                ).fetchone()
        if row is None:
            return None
        if payload is not None and row["command_payload"] != _json_text(payload):
            raise IdempotencyConflict()
        return _temporal_receipt_from_row(row)


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

    def reserve(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: tuple[UUID, UUID, Literal["create", "render", "publish"]],
        receipt: CommandReceipt,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Reserve one command receipt for a Temporal dispatch."""
        project_id, episode_id, command = identity
        for field, value in (
            ("tenant_id", tenant_id),
            ("project_id", project_id),
            ("episode_id", episode_id),
        ):
            _require_uuid7(value, field)
        _require_text(idempotency_key, "idempotency_key")
        if receipt.episode_id != episode_id or receipt.command != command:
            raise IdempotencyConflict()
        payload_text = _json_text(payload or {})
        with _write_connection(self._database) as connection:
            row = connection.execute(
                "SELECT * FROM command_receipts "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (str(tenant_id), idempotency_key),
            ).fetchone()
            if row is not None:
                existing = _receipt_from_row(row)
                if (
                    row["project_id"] != str(project_id)
                    or row["episode_id"] != str(episode_id)
                    or row["command"] != command
                ):
                    raise IdempotencyConflict()
                existing_payload = row["command_payload"]
                if not isinstance(existing_payload, str):
                    raise PersistenceIntegrityError(
                        "persisted command payload is invalid"
                    )
                if existing_payload != payload_text:
                    raise IdempotencyConflict()
                return existing
            try:
                connection.execute(
                    """INSERT INTO command_receipts (
                        tenant_id, project_id, episode_id, idempotency_key,
                        command_id, command, accepted, state, created_at,
                        workflow_id, command_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        idempotency_key,
                        str(receipt.command_id),
                        command,
                        int(receipt.accepted),
                        receipt.state,
                        _timestamp(receipt.created_at or datetime.now(UTC)),
                        receipt.workflow_id,
                        payload_text,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflict() from error
        return receipt

    def reconcile_dispatched(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: tuple[UUID, UUID, Literal["create", "render", "publish"]],
        receipt: CommandReceipt,
    ) -> CommandReceipt:
        """Persist Temporal acceptance without losing the workflow identity."""
        project_id, episode_id, command = identity
        with _write_connection(self._database) as connection:
            row = connection.execute(
                "SELECT * FROM command_receipts "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (str(tenant_id), idempotency_key),
            ).fetchone()
            if row is None:
                raise IdempotencyConflict()
            existing = _receipt_from_row(row)
            if (
                row["project_id"] != str(project_id)
                or row["episode_id"] != str(episode_id)
                or row["command"] != command
                or existing.command_id != receipt.command_id
            ):
                raise IdempotencyConflict()
            if existing.state == "dispatched":
                if existing.workflow_id != receipt.workflow_id:
                    raise IdempotencyConflict()
                return existing
            if existing.state != "queued" or receipt.state != "dispatched":
                raise IdempotencyConflict()
            connection.execute(
                "UPDATE command_receipts SET state = ?, workflow_id = ? "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (
                    receipt.state,
                    receipt.workflow_id,
                    str(tenant_id),
                    idempotency_key,
                ),
            )
        return receipt

    def submit(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: str,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
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
        if payload is not None and not isinstance(payload, Mapping):
            raise ValueError("command payload must be a mapping")
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
                existing_payload = row["command_payload"]
                if not isinstance(existing_payload, str):
                    raise PersistenceIntegrityError(
                        "persisted command payload is invalid"
                    )
                if existing_payload != _json_text(payload or {}):
                    raise IdempotencyConflict()
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
                        command_id, command, accepted, state, created_at,
                        workflow_id, command_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        receipt.workflow_id,
                        _json_text(payload or {}),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflict() from error
        return receipt

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID | None = None,
        episode_id: UUID,
        command: Literal["create", "render", "publish"],
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt | None:
        """Return an existing receipt without accepting a new command."""
        with _read_connection(self._database) as connection:
            if project_id is None:
                row = connection.execute(
                    """SELECT * FROM command_receipts
                       WHERE tenant_id = ? AND episode_id = ?
                       AND command = ? AND idempotency_key = ?""",
                    (str(tenant_id), str(episode_id), command, idempotency_key),
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT * FROM command_receipts
                       WHERE tenant_id = ? AND project_id = ? AND episode_id = ?
                       AND command = ? AND idempotency_key = ?""",
                    (
                        str(tenant_id),
                        str(project_id),
                        str(episode_id),
                        command,
                        idempotency_key,
                    ),
                ).fetchone()
        if row is None:
            return None
        if payload is not None:
            stored_payload = row["command_payload"]
            if not isinstance(stored_payload, str):
                raise PersistenceIntegrityError("persisted command payload is invalid")
            if stored_payload != _json_text(payload):
                raise IdempotencyConflict()
        return _receipt_from_row(row)


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


def _evidence_from_row(row: sqlite3.Row) -> ProviderEvidence:
    """Reconstruct normalized provider evidence and fail closed on corruption."""
    try:
        usage = json.loads(row["usage"])
        if not isinstance(usage, Mapping):
            raise ValueError("provider evidence usage must be a mapping")
        reconciled = (
            Decimal(row["reconciled_cost"])
            if row["reconciled_cost"] is not None
            else None
        )
        return ProviderEvidence(
            operation=row["operation"],
            provider=row["provider"],
            request_id=row["request_id"],
            model=row["model"],
            input_sha256=row["input_sha256"],
            output_sha256=row["output_sha256"],
            usage=usage,
            currency=row["currency"],
            estimated_cost=Decimal(row["estimated_cost"]),
            reconciled_cost=reconciled,
            latency_ms=row["latency_ms"],
            retry_count=row["retry_count"],
            occurred_at=_parse_timestamp(row["occurred_at"]),
            evidence_kind=row["evidence_kind"],
        )
    except (
        DecimalException,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise PersistenceIntegrityError(
            "persisted provider evidence is invalid"
        ) from error


def _evidence_values(tenant_id: UUID, evidence: ProviderEvidence) -> tuple[object, ...]:
    """Return normalized provider evidence values for one tenant row."""
    return (
        str(tenant_id),
        evidence.request_id,
        evidence.operation,
        evidence.provider,
        evidence.model,
        evidence.input_sha256,
        evidence.output_sha256,
        _json_text(evidence.usage),
        evidence.currency,
        format(evidence.estimated_cost, "f"),
        format(evidence.reconciled_cost, "f")
        if evidence.reconciled_cost is not None
        else None,
        evidence.latency_ms,
        evidence.retry_count,
        _timestamp(evidence.occurred_at),
        evidence.evidence_kind,
    )


def _require_bound_usage(evidence: ProviderEvidence, event: UsageEvent) -> None:
    """Require the usage row to describe exactly the same provider operation."""
    if (
        event.provider_request_id != evidence.request_id
        or event.operation != evidence.operation
        or event.units != evidence.usage
        or event.currency != evidence.currency
        or event.estimated_cost != evidence.estimated_cost
        or event.reconciled_cost != evidence.reconciled_cost
        or event.created_at != evidence.occurred_at
    ):
        raise UsageConflict("usage event is not bound to provider evidence")


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

    def record_evidence(
        self, evidence: ProviderEvidence, event: UsageEvent
    ) -> tuple[ProviderEvidence, UsageEvent]:
        """Atomically append or replay one evidence and cost pair."""
        if not isinstance(evidence, ProviderEvidence):
            raise ValueError("evidence must be ProviderEvidence")
        _require_bound_usage(evidence, event)
        with _write_connection(self._database) as connection:
            evidence_row = connection.execute(
                """SELECT * FROM provider_evidence
                   WHERE tenant_id = ? AND request_id = ?""",
                (str(event.tenant_id), evidence.request_id),
            ).fetchone()
            usage_row = connection.execute(
                """SELECT * FROM usage_events
                   WHERE tenant_id = ? AND provider_request_id = ?""",
                (str(event.tenant_id), evidence.request_id),
            ).fetchone()
            if evidence_row is not None or usage_row is not None:
                if evidence_row is None or usage_row is None:
                    raise PersistenceIntegrityError(
                        "provider evidence and usage event must be persisted together"
                    )
                persisted_evidence = _evidence_from_row(evidence_row)
                persisted_event = _usage_from_row(usage_row)
                if persisted_evidence != evidence or persisted_event != event:
                    raise UsageConflict()
                return persisted_evidence, persisted_event
            try:
                connection.execute(
                    """INSERT INTO provider_evidence (
                        tenant_id, request_id, operation, provider, model,
                        input_sha256, output_sha256, usage, currency,
                        estimated_cost, reconciled_cost, latency_ms, retry_count,
                        occurred_at, evidence_kind
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _evidence_values(event.tenant_id, evidence),
                )
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
        return evidence, event

    def get_evidence(
        self, tenant_id: UUID, provider_request_id: str
    ) -> ProviderEvidence:
        """Read one normalized provider-evidence record within a tenant."""
        _require_uuid7(tenant_id, "tenant_id")
        _require_text(provider_request_id, "provider_request_id")
        with _read_connection(self._database) as connection:
            row = connection.execute(
                """SELECT * FROM provider_evidence
                   WHERE tenant_id = ? AND request_id = ?""",
                (str(tenant_id), provider_request_id),
            ).fetchone()
        if row is None:
            raise UsageNotFound()
        return _evidence_from_row(row)

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


def _package_completion_from_json(value: object) -> PackageCompletion:
    """Reconstruct one persisted package completion or fail closed."""
    from poddown.audio.production_workflow import PackageCompletion

    try:
        if not isinstance(value, str):
            raise TypeError
        decoded = json.loads(value)
        if not isinstance(decoded, Mapping):
            raise TypeError
        return PackageCompletion.from_dict(decoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise PersistenceIntegrityError(
            "persisted package completion is invalid"
        ) from error


class SQLitePackageCompletionRepository:
    """Restart-safe SQLite repository for atomic package completion."""

    def __init__(self, database: Path | str, *, fail_next_commit: bool = False) -> None:
        self._database = _database_path(database)
        self._fail_next_commit = fail_next_commit
        _initialize_database(self._database)

    def commit_package_completion(
        self, completion: PackageCompletion
    ) -> PackageCompletion:
        """Insert or replay one immutable completion in one transaction."""
        from poddown.audio.production_workflow import (
            PackageCompletion,
            PackageCompletionConflictError,
            PackageCompletionError,
        )

        if not isinstance(completion, PackageCompletion):
            raise PackageCompletionError("package completion is required")
        episode_id = completion.final_master_qa.episode_id
        episode_version_id = completion.final_master_qa.episode_version_id
        with _write_connection(self._database) as connection:
            row = connection.execute(
                """SELECT completion_json FROM package_completions
                   WHERE episode_id = ? AND episode_version_id = ?""",
                (episode_id, episode_version_id),
            ).fetchone()
            if row is not None:
                existing = _package_completion_from_json(row["completion_json"])
                if existing != completion:
                    raise PackageCompletionConflictError(
                        "package completion identity conflicts with the existing record"
                    )
                return existing
            if self._fail_next_commit:
                self._fail_next_commit = False
                raise PackageCompletionError("atomic package commit failed")
            connection.execute(
                """INSERT INTO package_completions (
                       episode_id, episode_version_id, completion_json
                   ) VALUES (?, ?, ?)""",
                (episode_id, episode_version_id, _json_text(completion.to_dict())),
            )
        return completion

    def get(self, episode_id: str, episode_version_id: str) -> PackageCompletion | None:
        """Return one completed package or no record after restart."""
        _require_text(episode_id, "episode_id")
        _require_text(episode_version_id, "episode_version_id")
        with _read_connection(self._database) as connection:
            row = connection.execute(
                """SELECT completion_json FROM package_completions
                   WHERE episode_id = ? AND episode_version_id = ?""",
                (episode_id, episode_version_id),
            ).fetchone()
        if row is None:
            return None
        return _package_completion_from_json(row["completion_json"])


__all__ = [
    "PersistenceError",
    "PersistenceIntegrityError",
    "SQLiteCommandDispatcher",
    "SQLiteEpisodeRepository",
    "SQLitePackageCompletionRepository",
    "SQLiteTemporalCommandReceiptStore",
    "SQLiteUsageLedger",
    "UsageConflict",
    "UsageEvent",
    "UsageLedger",
    "UsageNotFound",
]
