"""Temporal integration evidence for the tenant-scoped outbox workflow."""

import asyncio
from typing import Any
from uuid import UUID

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.nats_outbox import OUTBOX_RELAY_ACTIVITY_NAME, OutboxRelayWorkflow
from tests.temporal_support import retry_local_temporal_environment

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")


def test_outbox_workflow_executes_with_temporal_wire_types() -> None:
    """The registered workflow must serialize its report through Temporal."""
    calls: list[dict[str, Any]] = []

    async def run_workflow() -> dict[str, Any]:
        @activity.defn(name=OUTBOX_RELAY_ACTIVITY_NAME)
        async def relay(payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return {"published_event_ids": [], "failed_event_ids": []}

        async with (
            retry_local_temporal_environment(
                WorkflowEnvironment.start_local
            ) as environment,
            Worker(
                environment.client,
                task_queue="poddown-outbox-workflow-integration",
                workflows=[OutboxRelayWorkflow],
                activities=[relay],
            ),
        ):
            return await environment.client.execute_workflow(
                OutboxRelayWorkflow.run,
                {"tenant_id": str(TENANT), "limit": 25},
                id="poddown-outbox-workflow-integration",
                task_queue="poddown-outbox-workflow-integration",
            )

    result = asyncio.run(run_workflow())

    assert result == {"published_event_ids": [], "failed_event_ids": []}
    assert calls == [{"tenant_id": str(TENANT), "limit": 25}]
