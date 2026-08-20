"""Transaction-bound PostgreSQL provider-evidence and usage persistence."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import TypeVar, cast
from uuid import UUID

from poddown.outbox import OutboxConnection, OutboxCursor
from poddown.persistence import UsageEvent, UsageLedger, UsageNotFound
from poddown.postgres_persistence import PostgresPersistenceConnection
from poddown.providers.contracts import ProviderEvidence


class PostgresLedgerError(ValueError):
    """Base error for provider-evidence ledger failures."""


class PostgresLedgerConflict(PostgresLedgerError, RuntimeError):
    """A provider request was replayed with different immutable evidence."""


class PostgresLedgerIntegrityError(PostgresLedgerError, RuntimeError):
    """The durable evidence and usage pair is incomplete or malformed."""


_ValueT = TypeVar("_ValueT")


def _set_tenant(cursor: OutboxCursor, tenant_id: UUID) -> None:
    cursor.execute(
        "SELECT set_config('app.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _mapping(value: object, message: str) -> Mapping[str, object]:
    parsed: object
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise PostgresLedgerIntegrityError(message) from error
    else:
        parsed = value
    if not isinstance(parsed, Mapping):
        raise PostgresLedgerIntegrityError(message)
    return parsed


def _int_mapping(value: object, message: str) -> Mapping[str, int]:
    parsed = _mapping(value, message)
    if not all(
        isinstance(key, str) and type(item) is int and item >= 0
        for key, item in parsed.items()
    ):
        raise PostgresLedgerIntegrityError(message)
    return cast(Mapping[str, int], parsed)


def _provider_evidence(
    tenant_id: UUID,
    row: Sequence[object],
) -> ProviderEvidence:
    if len(row) != 16:
        raise PostgresLedgerIntegrityError(
            "provider evidence row has an unexpected shape"
        )
    try:
        (
            project_id,
            job_id,
            request_id,
            operation,
            provider,
            model,
            input_sha256,
            output_sha256,
            usage,
            currency,
            estimated_cost,
            reconciled_cost,
            latency_ms,
            retry_count,
            occurred_at,
            evidence_kind,
        ) = row
        if not isinstance(project_id, UUID):
            project_id = UUID(str(project_id))
        if not isinstance(job_id, UUID):
            job_id = UUID(str(job_id))
        if not isinstance(request_id, str):
            raise TypeError("request_id")
        if not isinstance(operation, str):
            raise TypeError("operation")
        if not isinstance(provider, str):
            raise TypeError("provider")
        if not isinstance(model, str):
            raise TypeError("model")
        if not isinstance(evidence_kind, str):
            raise TypeError("evidence_kind")
        if project_id.version != 7 or job_id.version != 7:
            raise ValueError("scope")
        if not isinstance(input_sha256, str) or not isinstance(output_sha256, str):
            raise TypeError("hash")
        if not isinstance(currency, str):
            raise TypeError("currency")
        if not isinstance(latency_ms, int) or not isinstance(retry_count, int):
            raise TypeError("counters")
        if not hasattr(occurred_at, "tzinfo"):
            raise TypeError("occurred_at")
        return ProviderEvidence(
            operation=operation,
            provider=provider,
            request_id=request_id,
            model=model,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            usage=_int_mapping(usage, "provider evidence usage is invalid"),
            currency=currency,
            estimated_cost=Decimal(str(estimated_cost)),
            reconciled_cost=(
                Decimal(str(reconciled_cost)) if reconciled_cost is not None else None
            ),
            latency_ms=latency_ms,
            retry_count=retry_count,
            occurred_at=cast(datetime, occurred_at),
            evidence_kind=evidence_kind,
        )
    except (
        DecimalException,
        PostgresLedgerError,
        TypeError,
        ValueError,
    ) as error:
        if isinstance(error, PostgresLedgerError):
            raise
        raise PostgresLedgerIntegrityError(
            "provider evidence row is invalid"
        ) from error


def _usage_event(tenant_id: UUID, row: Sequence[object]) -> UsageEvent:
    if len(row) != 9:
        raise PostgresLedgerIntegrityError("usage row has an unexpected shape")
    try:
        (
            project_id,
            job_id,
            request_id,
            operation,
            units,
            currency,
            estimated_cost,
            reconciled_cost,
            created_at,
        ) = row
        if not isinstance(project_id, UUID):
            project_id = UUID(str(project_id))
        if not isinstance(job_id, UUID):
            job_id = UUID(str(job_id))
        if not isinstance(request_id, str) or not isinstance(operation, str):
            raise TypeError("usage identity")
        if not isinstance(currency, str) or not hasattr(created_at, "tzinfo"):
            raise TypeError("usage metadata")
        if project_id.version != 7 or job_id.version != 7:
            raise ValueError("scope")
        return UsageEvent(
            tenant_id=tenant_id,
            project_id=project_id,
            job_id=job_id,
            provider_request_id=request_id,
            operation=operation,
            units=_int_mapping(units, "usage units are invalid"),
            currency=currency,
            estimated_cost=Decimal(str(estimated_cost)),
            reconciled_cost=(
                Decimal(str(reconciled_cost)) if reconciled_cost is not None else None
            ),
            created_at=cast(datetime, created_at),
        )
    except (
        DecimalException,
        PostgresLedgerError,
        TypeError,
        ValueError,
    ) as error:
        if isinstance(error, PostgresLedgerError):
            raise
        raise PostgresLedgerIntegrityError("usage row is invalid") from error


_EVIDENCE_COLUMNS = (
    "project_id, job_id, request_id, operation, provider, model, input_sha256, "
    "output_sha256, usage, currency, estimated_cost, reconciled_cost, latency_ms, "
    "retry_count, occurred_at, evidence_kind"
)
_USAGE_COLUMNS = (
    "project_id, job_id, provider_request_id, operation, units, currency, "
    "estimated_cost, reconciled_cost, occurred_at"
)


class PostgresEvidenceLedger:
    """Persist provider evidence and its usage event in one caller transaction."""

    def record(
        self,
        connection: OutboxConnection,
        evidence: ProviderEvidence,
        event: UsageEvent,
    ) -> tuple[ProviderEvidence, UsageEvent]:
        """Insert or replay a complete pair without committing the transaction."""
        if not isinstance(evidence, ProviderEvidence) or not isinstance(
            event, UsageEvent
        ):
            raise PostgresLedgerError("evidence and event types are invalid")
        self._require_bound(evidence, event)
        cursor = connection.cursor()
        _set_tenant(cursor, event.tenant_id)
        existing_evidence = self._get_evidence(
            cursor, event.tenant_id, evidence.request_id
        )
        existing_event = self._get_usage(
            cursor, event.tenant_id, event.provider_request_id
        )
        if (existing_evidence is None) != (existing_event is None):
            raise PostgresLedgerIntegrityError(
                "provider evidence and usage event must be persisted together"
            )
        if existing_evidence is not None and existing_event is not None:
            if existing_evidence != evidence or existing_event != event:
                raise PostgresLedgerConflict("provider request evidence conflicts")
            return existing_evidence, existing_event
        cursor.execute(
            """INSERT INTO provider_evidence (
                tenant_id, project_id, job_id, request_id, operation, provider, model,
                input_sha256, output_sha256, usage, currency, estimated_cost,
                reconciled_cost, latency_ms, retry_count, occurred_at, evidence_kind
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, request_id) DO NOTHING""",
            (
                str(event.tenant_id),
                str(event.project_id),
                str(event.job_id),
                evidence.request_id,
                evidence.operation,
                evidence.provider,
                evidence.model,
                evidence.input_sha256,
                evidence.output_sha256,
                _json(dict(evidence.usage)),
                evidence.currency,
                evidence.estimated_cost,
                evidence.reconciled_cost,
                evidence.latency_ms,
                evidence.retry_count,
                evidence.occurred_at,
                evidence.evidence_kind,
            ),
        )
        cursor.execute(
            """INSERT INTO usage_events (
                tenant_id, project_id, job_id, provider_request_id, operation,
                units, currency, estimated_cost, reconciled_cost, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, provider_request_id) DO NOTHING""",
            (
                str(event.tenant_id),
                str(event.project_id),
                str(event.job_id),
                event.provider_request_id,
                event.operation,
                _json(dict(event.units)),
                event.currency,
                event.estimated_cost,
                event.reconciled_cost,
                event.created_at,
            ),
        )
        persisted_evidence = self._get_evidence(
            cursor, event.tenant_id, evidence.request_id
        )
        persisted_event = self._get_usage(
            cursor, event.tenant_id, event.provider_request_id
        )
        if persisted_evidence is None or persisted_event is None:
            raise PostgresLedgerIntegrityError(
                "provider evidence pair was not persisted"
            )
        if persisted_evidence != evidence or persisted_event != event:
            raise PostgresLedgerConflict("provider request evidence conflicts")
        return persisted_evidence, persisted_event

    def record_usage(
        self,
        connection: OutboxConnection,
        event: UsageEvent,
    ) -> UsageEvent:
        """Insert or replay one usage event in the caller's transaction."""
        if not isinstance(event, UsageEvent):
            raise PostgresLedgerError("usage event is invalid")
        cursor = connection.cursor()
        _set_tenant(cursor, event.tenant_id)
        existing = self._get_usage(cursor, event.tenant_id, event.provider_request_id)
        if existing is not None:
            if existing != event:
                raise PostgresLedgerConflict("provider request usage conflicts")
            return existing
        cursor.execute(
            """INSERT INTO usage_events (
                tenant_id, project_id, job_id, provider_request_id, operation,
                units, currency, estimated_cost, reconciled_cost, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, provider_request_id) DO NOTHING""",
            (
                str(event.tenant_id),
                str(event.project_id),
                str(event.job_id),
                event.provider_request_id,
                event.operation,
                _json(dict(event.units)),
                event.currency,
                event.estimated_cost,
                event.reconciled_cost,
                event.created_at,
            ),
        )
        persisted = self._get_usage(cursor, event.tenant_id, event.provider_request_id)
        if persisted is None:
            raise PostgresLedgerIntegrityError("usage event was not persisted")
        if persisted != event:
            raise PostgresLedgerConflict("provider request usage conflicts")
        return persisted

    def get_usage(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        provider_request_id: str,
    ) -> UsageEvent:
        """Read one tenant-scoped usage event in the caller's transaction."""
        cursor = connection.cursor()
        _set_tenant(cursor, tenant_id)
        event = self._get_usage(cursor, tenant_id, provider_request_id)
        if event is None:
            raise UsageNotFound()
        return event

    def list_usage(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        job_id: UUID,
    ) -> tuple[UsageEvent, ...]:
        """Read one tenant/job's usage events in stable creation order."""
        cursor = connection.cursor()
        _set_tenant(cursor, tenant_id)
        cursor.execute(
            f"SELECT {_USAGE_COLUMNS} FROM usage_events "
            "WHERE tenant_id = %s AND job_id = %s "
            "ORDER BY occurred_at, provider_request_id",
            (str(tenant_id), str(job_id)),
        )
        return tuple(_usage_event(tenant_id, row) for row in cursor.fetchall())

    def get_evidence(
        self,
        connection: OutboxConnection,
        tenant_id: UUID,
        provider_request_id: str,
    ) -> ProviderEvidence:
        """Read one tenant-scoped provider evidence record."""
        cursor = connection.cursor()
        _set_tenant(cursor, tenant_id)
        evidence = self._get_evidence(cursor, tenant_id, provider_request_id)
        if evidence is None:
            raise UsageNotFound()
        return evidence

    @staticmethod
    def _require_bound(evidence: ProviderEvidence, event: UsageEvent) -> None:
        if (
            evidence.request_id != event.provider_request_id
            or evidence.operation != event.operation
            or dict(evidence.usage) != dict(event.units)
            or evidence.currency != event.currency
            or evidence.estimated_cost != event.estimated_cost
            or evidence.reconciled_cost != event.reconciled_cost
            or evidence.occurred_at != event.created_at
        ):
            raise PostgresLedgerConflict("provider evidence is not bound to usage")

    @staticmethod
    def _get_evidence(
        cursor: OutboxCursor,
        tenant_id: UUID,
        request_id: str,
    ) -> ProviderEvidence | None:
        cursor.execute(
            f"SELECT {_EVIDENCE_COLUMNS} FROM provider_evidence "
            "WHERE tenant_id = %s AND request_id = %s",
            (str(tenant_id), request_id),
        )
        row = cursor.fetchone()
        return _provider_evidence(tenant_id, row) if row is not None else None

    @staticmethod
    def _get_usage(
        cursor: OutboxCursor,
        tenant_id: UUID,
        request_id: str,
    ) -> UsageEvent | None:
        cursor.execute(
            f"SELECT {_USAGE_COLUMNS} FROM usage_events "
            "WHERE tenant_id = %s AND provider_request_id = %s",
            (str(tenant_id), request_id),
        )
        row = cursor.fetchone()
        return _usage_event(tenant_id, row) if row is not None else None


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


class PostgresUsageLedger(UsageLedger):
    """Open transaction-scoped PostgreSQL connections for usage operations."""

    def __init__(
        self,
        connection_factory: Callable[[], PostgresPersistenceConnection],
        *,
        evidence_ledger: PostgresEvidenceLedger | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self._evidence_ledger = evidence_ledger or PostgresEvidenceLedger()

    def _transaction(
        self,
        operation: Callable[[PostgresPersistenceConnection], _ValueT],
    ) -> _ValueT:
        connection = self._connection_factory()
        try:
            with connection.transaction():
                return operation(connection)
        finally:
            _close(connection)

    def record(self, event: UsageEvent) -> UsageEvent:
        """Append or replay one immutable usage event."""
        return self._transaction(
            lambda connection: self._evidence_ledger.record_usage(connection, event)
        )

    def record_evidence(
        self,
        evidence: ProviderEvidence,
        event: UsageEvent,
    ) -> tuple[ProviderEvidence, UsageEvent]:
        """Append or replay provider evidence and usage atomically."""
        return self._transaction(
            lambda connection: self._evidence_ledger.record(connection, evidence, event)
        )

    def get(self, tenant_id: UUID, provider_request_id: str) -> UsageEvent:
        """Read one usage event within the caller's tenant scope."""
        return self._transaction(
            lambda connection: self._evidence_ledger.get_usage(
                connection, tenant_id, provider_request_id
            )
        )

    def get_evidence(
        self,
        tenant_id: UUID,
        provider_request_id: str,
    ) -> ProviderEvidence:
        """Read one provider evidence record within the caller's tenant scope."""
        return self._transaction(
            lambda connection: self._evidence_ledger.get_evidence(
                connection, tenant_id, provider_request_id
            )
        )

    def list_for_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
    ) -> tuple[UsageEvent, ...]:
        """List one tenant-scoped job's usage events in stable order."""
        return self._transaction(
            lambda connection: self._evidence_ledger.list_usage(
                connection, tenant_id, job_id
            )
        )


__all__ = [
    "PostgresEvidenceLedger",
    "PostgresLedgerConflict",
    "PostgresLedgerError",
    "PostgresLedgerIntegrityError",
    "PostgresUsageLedger",
]
