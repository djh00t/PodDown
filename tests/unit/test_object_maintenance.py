"""Unit contracts for the fail-closed object-maintenance activity."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from temporalio.exceptions import ApplicationError

from poddown.object_maintenance import (
    ObjectMaintenanceRequest,
    build_object_maintenance_activity,
)
from poddown.postgres_objects import OrphanCollectionReport

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
NOW = datetime(2026, 8, 15, 12, tzinfo=UTC)


class _Maintenance:
    def __init__(self, result: object) -> None:
        self.result = result

    def collect_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        now: datetime,
        grace_period: timedelta,
        observed_at: datetime,
    ) -> OrphanCollectionReport:
        del tenant_id, project_id, now, grace_period, observed_at
        if isinstance(self.result, BaseException):
            raise self.result
        return cast(OrphanCollectionReport, self.result)


def _payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": str(TENANT),
        "project_id": str(PROJECT),
        "grace_period_seconds": 86_400,
    }
    value.update(overrides)
    return value


def _valid_report() -> OrphanCollectionReport:
    return OrphanCollectionReport(
        discovered_keys=("old", "retained"),
        deleted_keys=("old",),
        retained_keys=("retained",),
    )


def _activity(
    result: object = _valid_report(),
    *,
    clock: object = lambda: NOW,
) -> Any:
    return build_object_maintenance_activity(
        _Maintenance(result),
        clock=cast(Any, clock),
    )


def test_request_round_trips_as_compact_json_and_is_immutable() -> None:
    request = ObjectMaintenanceRequest.from_payload(_payload())
    uuid_payload = _payload(tenant_id=TENANT, project_id=PROJECT)
    assert ObjectMaintenanceRequest.from_payload(uuid_payload) == request

    assert request.to_dict() == _payload()
    with pytest.raises(AttributeError):
        request.grace_period_seconds = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "object maintenance payload"),
        (
            {"tenant_id": None, "project_id": str(PROJECT), "grace_period_seconds": 0},
            "tenant_id",
        ),
        (_payload(tenant_id="not-a-uuid"), "tenant_id"),
        (_payload(project_id=object()), "project_id"),
        (_payload(grace_period_seconds=None), "grace_period_seconds"),
        (_payload(grace_period_seconds=True), "grace_period_seconds"),
        (_payload(grace_period_seconds=10**30), "too large"),
    ],
)
def test_request_rejects_invalid_payload(payload: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ObjectMaintenanceRequest.from_payload(cast(Any, payload))


@pytest.mark.parametrize(
    "value",
    [
        {"tenant_id": "not-a-uuid", "project_id": PROJECT, "grace_period_seconds": 0},
        {"tenant_id": TENANT, "project_id": "not-a-uuid", "grace_period_seconds": 0},
        {"tenant_id": TENANT, "project_id": PROJECT, "grace_period_seconds": True},
    ],
)
def test_request_constructor_rejects_invalid_types(value: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ObjectMaintenanceRequest(**cast(Any, value))


def test_builder_rejects_invalid_dependencies() -> None:
    with pytest.raises(TypeError, match="collect_project"):
        build_object_maintenance_activity(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="clock"):
        build_object_maintenance_activity(_Maintenance(_valid_report()), clock=object())  # type: ignore[arg-type]


def test_activity_rejects_a_clock_without_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(_activity(clock=lambda: datetime(2026, 8, 15, 12))(_payload()))


@pytest.mark.parametrize(
    "result",
    [
        object(),
        OrphanCollectionReport(("old",), ("missing",), ()),
        OrphanCollectionReport(("same",), ("same",), ("same",)),
        OrphanCollectionReport(("old",), ("old",), ["old"]),  # type: ignore[arg-type]
    ],
)
def test_activity_rejects_malformed_cleanup_evidence(result: object) -> None:
    with pytest.raises(ApplicationError) as error:
        asyncio.run(_activity(result)(_payload()))
    assert error.value.type == "ObjectMaintenanceExecutionError"
    assert error.value.non_retryable is True


def test_activity_maps_cleanup_validation_failure_to_non_retryable_error() -> None:
    with pytest.raises(ApplicationError) as error:
        asyncio.run(_activity(ValueError("invalid cleanup"))(_payload()))
    assert error.value.type == "ObjectMaintenanceExecutionError"
