"""Durable Temporal boundary for explicit, reference-aware object cleanup."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from poddown.postgres_objects import OrphanCollectionReport

OBJECT_MAINTENANCE_ACTIVITY_NAME = "maintain_objects"
_VALIDATION_MESSAGE = "object maintenance command rejected"
_VALIDATION_TYPE = "ObjectMaintenanceValidationError"
_EXECUTION_MESSAGE = "object maintenance returned malformed evidence"
_EXECUTION_TYPE = "ObjectMaintenanceExecutionError"


class ObjectMaintenancePort(Protocol):
    """Reference-aware cleanup surface used by the worker activity."""

    def collect_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        now: datetime,
        grace_period: timedelta,
        observed_at: datetime,
    ) -> OrphanCollectionReport:
        """Run one scoped inventory and conservative deletion pass."""


Clock = Callable[[], datetime]


def _required_uuid(name: str, value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a UUID string") from error


def _required_grace_period(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("grace_period_seconds must be a non-negative integer")
    try:
        timedelta(seconds=value)
    except OverflowError as error:
        raise ValueError("grace_period_seconds is too large") from error
    return value


def _required_timestamp(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("maintenance clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _string_tuple(name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{name} evidence is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ObjectMaintenanceRequest:
    """Immutable, tenant-scoped command accepted by the maintenance activity."""

    tenant_id: UUID
    project_id: UUID
    grace_period_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, UUID):
            raise ValueError("tenant_id must be a UUID")
        if not isinstance(self.project_id, UUID):
            raise ValueError("project_id must be a UUID")
        if type(self.grace_period_seconds) is not int or self.grace_period_seconds < 0:
            raise ValueError("grace_period_seconds must be a non-negative integer")
        _required_grace_period(self.grace_period_seconds)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ObjectMaintenanceRequest:
        """Validate the compact JSON payload without accepting caller timestamps."""
        if not isinstance(payload, Mapping):
            raise ValueError("object maintenance payload must be an object")
        return cls(
            tenant_id=_required_uuid("tenant_id", payload.get("tenant_id")),
            project_id=_required_uuid("project_id", payload.get("project_id")),
            grace_period_seconds=_required_grace_period(
                payload.get("grace_period_seconds")
            ),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-native worker payload shape."""
        return {
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "grace_period_seconds": self.grace_period_seconds,
        }


def _report_payload(
    request: ObjectMaintenanceRequest,
    report: OrphanCollectionReport,
    observed_at: datetime,
) -> dict[str, object]:
    if not isinstance(report, OrphanCollectionReport):
        raise ValueError("object maintenance report has an invalid type")
    discovered = _string_tuple("discovered", report.discovered_keys)
    deleted = _string_tuple("deleted", report.deleted_keys)
    retained = _string_tuple("retained", report.retained_keys)
    if set(deleted) & set(retained) or not set(deleted) <= set(discovered):
        raise ValueError("object maintenance report has inconsistent keys")
    return {
        "tenant_id": str(request.tenant_id),
        "project_id": str(request.project_id),
        "grace_period_seconds": request.grace_period_seconds,
        "observed_at": observed_at.isoformat(),
        "discovered_keys": list(discovered),
        "deleted_keys": list(deleted),
        "retained_keys": list(retained),
    }


def build_object_maintenance_activity(
    maintenance: ObjectMaintenancePort,
    *,
    clock: Clock | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build an explicit Temporal activity over the cleanup port.

    The activity accepts no caller-supplied timestamp. The worker clock is the
    source of truth for both observation and grace-period evaluation, while
    storage and reference failures remain retryable by Temporal.
    """
    if not hasattr(maintenance, "collect_project"):
        raise TypeError("maintenance must implement collect_project")
    if clock is not None and not callable(clock):
        raise TypeError("clock must be callable")
    current_time = clock or (lambda: datetime.now(UTC))

    @activity.defn(name=OBJECT_MAINTENANCE_ACTIVITY_NAME)
    async def maintain_objects(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = ObjectMaintenanceRequest.from_payload(payload)
        except (TypeError, ValueError) as error:
            raise ApplicationError(
                _VALIDATION_MESSAGE,
                type=_VALIDATION_TYPE,
                non_retryable=True,
            ) from error
        observed_at = _required_timestamp(current_time())
        try:
            report = maintenance.collect_project(
                request.tenant_id,
                request.project_id,
                now=observed_at,
                grace_period=timedelta(seconds=request.grace_period_seconds),
                observed_at=observed_at,
            )
            return _report_payload(request, report, observed_at)
        except ValueError as error:
            raise ApplicationError(
                _EXECUTION_MESSAGE,
                type=_EXECUTION_TYPE,
                non_retryable=True,
            ) from error

    return maintain_objects


__all__ = [
    "OBJECT_MAINTENANCE_ACTIVITY_NAME",
    "ObjectMaintenancePort",
    "ObjectMaintenanceRequest",
    "build_object_maintenance_activity",
]
