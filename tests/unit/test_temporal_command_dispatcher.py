"""Unit tests for deterministic Temporal workflow identity and replay."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from poddown.api.temporal_dispatcher import (
    TemporalClientTransport,
    TemporalCommandDispatcher,
    TemporalCommandRequest,
    TemporalWorkflowAlreadyStarted,
    TemporalWorkflowStatus,
    workflow_id_for_command,
)
from poddown.persistence import SQLiteCommandDispatcher


def test_workflow_identity_binds_tenant_project_episode_command_and_key() -> None:
    values = {
        "tenant_id": UUID("018f2c8b-7b46-7cc5-b2e1-111111111111"),
        "project_id": UUID("018f2c8b-7b46-7cc5-b2e1-222222222222"),
        "episode_id": UUID("018f2c8b-7b46-7cc5-b2e1-333333333333"),
        "command": "render",
        "idempotency_key": "key-1",
    }

    first = workflow_id_for_command(**values)
    second = workflow_id_for_command(**values)

    assert first == second
    assert first.startswith("poddown-command-")
    assert "key-1" not in first


class _RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[TemporalCommandRequest] = []

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        self.requests.append(request)


def test_sqlite_receipt_store_survives_temporal_dispatcher_restart(
    tmp_path,
) -> None:
    database = tmp_path / "receipts.sqlite3"
    store = SQLiteCommandDispatcher(database)
    first_transport = _RecordingTransport()
    first_dispatcher = TemporalCommandDispatcher(
        first_transport,
        task_queue="poddown-default",
        receipt_store=store,
    )
    values = {
        "tenant_id": UUID("018f2c8b-7b46-7cc5-b2e1-111111111111"),
        "project_id": UUID("018f2c8b-7b46-7cc5-b2e1-222222222222"),
        "episode_id": UUID("01986e76-4ec6-7a8f-8000-000000000001"),
        "command": "publish",
        "idempotency_key": "publish-1",
        "payload": {"target_id": "minio-demo", "approval_id": "approval-1"},
    }

    first = first_dispatcher.submit(**values)

    second_transport = _RecordingTransport()
    second_dispatcher = TemporalCommandDispatcher(
        second_transport,
        task_queue="poddown-default",
        receipt_store=SQLiteCommandDispatcher(database),
    )
    second = second_dispatcher.submit(**values)

    assert first == second
    assert first.state == "dispatched"
    assert len(first_transport.requests) == 1
    assert second_transport.requests == []


def test_temporal_client_transport_normalizes_duplicate_from_an_async_handler() -> None:
    class _DuplicateClient:
        async def start_workflow(self, *_args, **kwargs) -> None:
            raise WorkflowAlreadyStartedError(kwargs["id"], "EpisodeRenderWorkflow")

    async def factory(_address: str, *, namespace: str) -> _DuplicateClient:
        assert namespace == "default"
        return _DuplicateClient()

    transport = TemporalClientTransport("temporal:7233", client_factory=factory)
    request = TemporalCommandRequest(
        workflow_id="poddown-command-existing",
        task_queue="poddown-default",
        payload="{}",
    )

    async def call_from_async_handler() -> None:
        with pytest.raises(TemporalWorkflowAlreadyStarted) as error:
            transport.start_workflow(request)
        assert str(error.value).endswith("poddown-command-existing")

    import asyncio

    asyncio.run(call_from_async_handler())


def test_temporal_client_transport_reads_completed_workflow_result() -> None:
    """The status port must expose a safe completed result from Temporal."""

    class _Handle:
        async def describe(self) -> SimpleNamespace:
            return SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)

        async def result(self) -> str:
            return '{"status":"completed","stage":"packaged"}'

    class _Client:
        def get_workflow_handle(self, workflow_id: str) -> _Handle:
            assert workflow_id == "episode-production-1"
            return _Handle()

    async def factory(_address: str, *, namespace: str) -> _Client:
        assert namespace == "default"
        return _Client()

    transport = TemporalClientTransport("temporal:7233", client_factory=factory)

    status = transport.get_workflow_status("episode-production-1")

    assert status == TemporalWorkflowStatus(
        state="completed", result='{"status":"completed","stage":"packaged"}'
    )
