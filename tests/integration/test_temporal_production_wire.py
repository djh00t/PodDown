"""Temporal wire-contract evidence for unavailable production activities."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from poddown.audio.production_workflow import (
    PRODUCTION_ACTIVITY_ERROR_TYPE,
    build_unavailable_production_activity,
)
from tests.temporal_support import retry_local_temporal_environment

ACTIVITY_NAME = "poddown.test.unavailable-production-stage"
TASK_QUEUE = "poddown-temporal-production-wire"


@workflow.defn(name="InvokeUnavailableProductionActivityWorkflow")
class InvokeUnavailableProductionActivityWorkflow:
    """Invoke one unavailable stage so the worker boundary is exercised."""

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> None:
        await workflow.execute_activity(
            ACTIVITY_NAME,
            args=[payload],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


def test_unavailable_production_activity_preserves_typed_failure() -> None:
    """Wire decoding must reach the fail-closed activity implementation."""

    async def run_workflow() -> WorkflowFailureError:
        activity_handler = build_unavailable_production_activity(ACTIVITY_NAME)
        async with (
            retry_local_temporal_environment(
                WorkflowEnvironment.start_local
            ) as environment,
            Worker(
                environment.client,
                task_queue=TASK_QUEUE,
                workflows=[InvokeUnavailableProductionActivityWorkflow],
                activities=[activity_handler],
            ),
        ):
            with pytest.raises(WorkflowFailureError) as error:
                await environment.client.execute_workflow(
                    InvokeUnavailableProductionActivityWorkflow.run,
                    {"stage": "package"},
                    id="poddown-unavailable-production-stage",
                    task_queue=TASK_QUEUE,
                )
            return error.value

    workflow_error = asyncio.run(run_workflow())
    cause: BaseException = workflow_error.cause
    while cause.__cause__ is not None:
        cause = cause.__cause__
    assert isinstance(cause, ApplicationError)
    assert cause.type == PRODUCTION_ACTIVITY_ERROR_TYPE
    assert cause.non_retryable is True
