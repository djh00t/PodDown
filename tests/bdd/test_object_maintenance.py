"""BDD coverage for the durable object-maintenance worker boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when
from temporalio.exceptions import ApplicationError

from poddown.object_maintenance import (
    OBJECT_MAINTENANCE_ACTIVITY_NAME,
    ObjectMaintenanceRequest,
    build_object_maintenance_activity,
)
from poddown.postgres_objects import OrphanCollectionReport

scenarios("../features/object_maintenance.feature")

TENANT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
NOW = datetime(2026, 8, 15, 12, tzinfo=UTC)


class _Maintenance:
    def __init__(self) -> None:
        self.calls: list[ObjectMaintenanceRequest] = []

    def collect_project(
        self,
        tenant_id: UUID,
        project_id: UUID,
        *,
        now: datetime,
        grace_period: timedelta,
        observed_at: datetime,
    ) -> OrphanCollectionReport:
        self.calls.append(
            ObjectMaintenanceRequest(
                tenant_id=tenant_id,
                project_id=project_id,
                grace_period_seconds=int(grace_period.total_seconds()),
            )
        )
        assert now == NOW
        assert observed_at == NOW
        return OrphanCollectionReport(
            discovered_keys=("old-referenced", "old-unreferenced", "young"),
            deleted_keys=("old-unreferenced",),
            retained_keys=("old-referenced", "young"),
        )


@given("an explicit object maintenance activity")
def explicit_activity(context: Any) -> None:
    maintenance = _Maintenance()
    context.values["maintenance"] = maintenance
    context.values["activity"] = build_object_maintenance_activity(
        maintenance,
        clock=lambda: NOW,
    )


@when("the object maintenance command runs")
def run_command(context: Any) -> None:
    payload = {
        "tenant_id": str(TENANT),
        "project_id": str(PROJECT),
        "grace_period_seconds": 86_400,
    }
    context.values["result"] = asyncio.run(context.values["activity"](payload))


@then("the activity returns the scoped cleanup report")
def report_is_scoped(context: Any) -> None:
    result = context.values["result"]
    assert result == {
        "tenant_id": str(TENANT),
        "project_id": str(PROJECT),
        "grace_period_seconds": 86_400,
        "observed_at": NOW.isoformat(),
        "discovered_keys": ["old-referenced", "old-unreferenced", "young"],
        "deleted_keys": ["old-unreferenced"],
        "retained_keys": ["old-referenced", "young"],
    }
    assert context.values["maintenance"].calls[0].tenant_id == TENANT
    assert context.values["maintenance"].calls[0].project_id == PROJECT


@when("the object maintenance command has an invalid grace period")
def invalid_grace_period(context: Any) -> None:
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            context.values["activity"](
                {
                    "tenant_id": str(TENANT),
                    "project_id": str(PROJECT),
                    "grace_period_seconds": -1,
                }
            )
        )
    context.values["error"] = error.value


@then("the object maintenance activity rejects the command")
def command_rejected(context: Any) -> None:
    error = context.values["error"]
    assert error.type == "ObjectMaintenanceValidationError"
    assert error.non_retryable is True
    assert context.values["maintenance"].calls == []


assert OBJECT_MAINTENANCE_ACTIVITY_NAME == "maintain_objects"
