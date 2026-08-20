"""Temporal integration evidence for reference-aware object maintenance."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.object_maintenance import (
    OBJECT_MAINTENANCE_ACTIVITY_NAME,
    build_object_maintenance_activity,
)
from poddown.postgres_objects import OrphanCollectionReport
from tests.temporal_support import retry_local_temporal_environment

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
NOW = datetime(2026, 8, 15, 12, tzinfo=UTC)
TASK_QUEUE = "poddown-object-maintenance-integration"


@workflow.defn(name="InvokeObjectMaintenanceForTest")
class InvokeObjectMaintenanceForTest:
    """Test-only workflow that crosses the real activity payload boundary."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            OBJECT_MAINTENANCE_ACTIVITY_NAME,
            args=[payload],
            start_to_close_timeout=timedelta(seconds=30),
        )


class _Maintenance:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "now": now,
                "grace_period": grace_period,
                "observed_at": observed_at,
            }
        )
        return OrphanCollectionReport(
            discovered_keys=("old-unreferenced", "young"),
            deleted_keys=("old-unreferenced",),
            retained_keys=("young",),
        )


def test_object_maintenance_activity_crosses_temporal_wire() -> None:
    """The worker clock and scoped cleanup report survive Temporal serialization."""

    async def run_workflow() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        maintenance = _Maintenance()
        activity = build_object_maintenance_activity(
            maintenance,
            clock=lambda: NOW,
        )
        async with (
            retry_local_temporal_environment(
                WorkflowEnvironment.start_local
            ) as environment,
            Worker(
                environment.client,
                task_queue=TASK_QUEUE,
                workflows=[InvokeObjectMaintenanceForTest],
                activities=[activity],
            ),
        ):
            result = await environment.client.execute_workflow(
                InvokeObjectMaintenanceForTest.run,
                {
                    "tenant_id": str(TENANT),
                    "project_id": str(PROJECT),
                    "grace_period_seconds": 86_400,
                },
                id="poddown-object-maintenance-integration",
                task_queue=TASK_QUEUE,
            )
            return result, maintenance.calls

    result, calls = asyncio.run(run_workflow())

    assert result == {
        "tenant_id": str(TENANT),
        "project_id": str(PROJECT),
        "grace_period_seconds": 86_400,
        "observed_at": NOW.isoformat(),
        "discovered_keys": ["old-unreferenced", "young"],
        "deleted_keys": ["old-unreferenced"],
        "retained_keys": ["young"],
    }
    assert calls == [
        {
            "tenant_id": TENANT,
            "project_id": PROJECT,
            "now": NOW,
            "grace_period": timedelta(days=1),
            "observed_at": NOW,
        }
    ]
