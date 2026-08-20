"""BDD bindings for the deterministic Temporal command dispatcher."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from pytest_bdd import given, scenarios, then, when

from poddown.api.temporal_dispatcher import (
    TemporalCommandDispatcher,
    TemporalCommandRequest,
)

scenarios("../features/temporal_command_dispatcher.feature")

TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")
EPISODE = UUID("018f2c8b-7b46-7cc5-b2e1-333333333333")


class _Transport:
    def __init__(self) -> None:
        self.requests: list[TemporalCommandRequest] = []

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        self.requests.append(request)


@given("a recording Temporal command transport")
def recording_transport(context) -> None:
    transport = _Transport()
    context.values["transport"] = transport
    context.values["dispatcher"] = TemporalCommandDispatcher(
        transport,
        task_queue="poddown-default",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


@when("I submit the same render command twice")
def submit_twice(context) -> None:
    dispatcher = context.values["dispatcher"]
    values = {
        "tenant_id": TENANT,
        "project_id": PROJECT,
        "episode_id": EPISODE,
        "command": "render",
        "idempotency_key": "render-command-1",
    }
    context.values["receipts"] = (
        dispatcher.submit(**values),
        dispatcher.submit(**values),
    )


@then("the transport receives one deterministic workflow start")
def one_workflow_start(context) -> None:
    requests = context.values["transport"].requests
    assert len(requests) == 1
    assert requests[0].workflow_id.startswith("poddown-command-")
    assert requests[0].task_queue == "poddown-default"


@then("both receipts identify the dispatched workflow")
def receipts_are_dispatched(context) -> None:
    first, second = context.values["receipts"]
    assert first.state == "dispatched"
    assert first.workflow_id == second.workflow_id
    assert first.workflow_id == context.values["transport"].requests[0].workflow_id


@when("I submit a publish command with a target and approval")
def submit_publish(context) -> None:
    context.values["dispatcher"].submit(
        tenant_id=TENANT,
        project_id=PROJECT,
        episode_id=EPISODE,
        command="publish",
        idempotency_key="publish-command-1",
        payload={"target_id": "minio-demo", "approval_id": "approval-1"},
    )


@then("the workflow payload contains the publication scope")
def workflow_payload_contains_scope(context) -> None:
    payload = json.loads(context.values["transport"].requests[-1].payload)
    assert payload["payload"] == {
        "approval_id": "approval-1",
        "target_id": "minio-demo",
    }
