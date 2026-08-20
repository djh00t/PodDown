"""Deterministic Temporal adapter for asynchronous Episode API commands."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock, Thread
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from uuid6 import uuid7

from poddown.api.models import CommandReceipt
from poddown.api.runtime import CommandName
from poddown.audio.workflow import EpisodeCommandWorkflow
from poddown.episode_service import IdempotencyConflict

CommandIdentity = tuple[UUID, UUID, CommandName]
ReceiptKey = tuple[UUID, str]


@dataclass(frozen=True, slots=True)
class TemporalCommandRequest:
    """Immutable, JSON-safe request for starting one Temporal workflow."""

    workflow_id: str
    task_queue: str
    payload: str


TemporalWorkflowState = Literal[
    "running",
    "completed",
    "failed",
    "canceled",
    "terminated",
    "continued_as_new",
    "timed_out",
]


@dataclass(frozen=True, slots=True)
class TemporalWorkflowStatus:
    """Safe status and optional JSON result read from one Temporal workflow."""

    state: TemporalWorkflowState
    result: str | None = None


class TemporalWorkflowAlreadyStarted(RuntimeError):
    """Transport-normalized signal that the deterministic workflow already exists."""

    def __init__(self, workflow_id: str) -> None:
        super().__init__(f"Temporal workflow already started: {workflow_id}")
        self.workflow_id = workflow_id


class TemporalCommandTransport(Protocol):
    """Port for the one Temporal operation required by command dispatch."""

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        """Start the workflow or raise a normalized transport error."""


class TemporalWorkflowClient(Protocol):
    """Minimal async Temporal client surface used by the production adapter."""

    async def start_workflow(
        self,
        workflow: object,
        arg: object,
        *,
        id: str,
        task_queue: str,
        id_reuse_policy: WorkflowIDReusePolicy,
    ) -> object:
        """Start one workflow execution."""


class TemporalWorkflowHandle(Protocol):
    """Minimal Temporal handle surface required by the status reader."""

    async def describe(self) -> object:
        """Return the workflow execution description."""

    async def result(self) -> object:
        """Return the terminal workflow result."""


class TemporalWorkflowStatusClient(Protocol):
    """Async Temporal client surface required by the status reader."""

    def get_workflow_handle(self, workflow_id: str) -> TemporalWorkflowHandle:
        """Return a handle for one workflow execution."""


TemporalClientFactory = Callable[..., Awaitable[TemporalWorkflowClient]]


def _run_coroutine(operation: Callable[[], Any]) -> Any:
    """Run an async Temporal call from both sync and async API handlers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(operation())

    result: list[Any] = []
    error: list[BaseException] = []

    def run_in_thread() -> None:
        try:
            result.append(asyncio.run(operation()))
        except BaseException as exception:  # pragma: no cover - re-raised below
            error.append(exception)

    thread = Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    if not result:
        raise RuntimeError("Temporal operation returned no result")
    return result[0]


async def _connect_temporal_client(
    address: str, *, namespace: str
) -> TemporalWorkflowClient:
    """Connect one short-lived client for a command dispatch."""
    return cast(
        TemporalWorkflowClient,
        await Client.connect(address, namespace=namespace),
    )


class TemporalClientTransport:
    """Bridge the synchronous command port to the Temporal async client.

    The API command port is intentionally synchronous because it is also used by
    local and SQLite adapters. This adapter owns the small async bridge and
    creates a client for each accepted command, avoiding an event-loop-bound
    client shared between FastAPI worker threads.
    """

    def __init__(
        self,
        address: str,
        *,
        namespace: str = "default",
        client_factory: TemporalClientFactory | None = None,
        workflow: object = EpisodeCommandWorkflow.run,
    ) -> None:
        if not isinstance(address, str) or not address.strip():
            raise ValueError("Temporal address must be non-empty")
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("Temporal namespace must be non-empty")
        if workflow is None:
            raise ValueError("Temporal workflow must be configured")
        self._address = address.strip()
        self._namespace = namespace.strip()
        self._client_factory = client_factory or _connect_temporal_client
        self._workflow = workflow

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        """Start the immutable episode workflow and normalize duplicate starts."""
        if not isinstance(request, TemporalCommandRequest):
            raise TypeError("request must be a TemporalCommandRequest")

        async def operation() -> None:
            client = await self._client_factory(
                self._address, namespace=self._namespace
            )
            try:
                await client.start_workflow(
                    self._workflow,
                    request.payload,
                    id=request.workflow_id,
                    task_queue=request.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError as error:
                raise TemporalWorkflowAlreadyStarted(error.workflow_id) from error

        _run_coroutine(operation)

    def get_workflow_status(self, workflow_id: str) -> TemporalWorkflowStatus:
        """Read one workflow state and its safe terminal JSON result."""
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("workflow_id must be non-empty")

        async def operation() -> TemporalWorkflowStatus:
            client = await self._client_factory(
                self._address, namespace=self._namespace
            )
            status_client = cast(TemporalWorkflowStatusClient, client)
            handle = status_client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            execution_status = getattr(description, "status", None)
            if not isinstance(execution_status, WorkflowExecutionStatus):
                raise RuntimeError("Temporal returned an unknown workflow status")
            state_by_execution: dict[WorkflowExecutionStatus, TemporalWorkflowState] = {
                WorkflowExecutionStatus.RUNNING: "running",
                WorkflowExecutionStatus.COMPLETED: "completed",
                WorkflowExecutionStatus.FAILED: "failed",
                WorkflowExecutionStatus.CANCELED: "canceled",
                WorkflowExecutionStatus.TERMINATED: "terminated",
                WorkflowExecutionStatus.CONTINUED_AS_NEW: "continued_as_new",
                WorkflowExecutionStatus.TIMED_OUT: "timed_out",
            }
            state = state_by_execution[execution_status]
            if state != "completed":
                return TemporalWorkflowStatus(state=state)
            result = await handle.result()
            if not isinstance(result, str):
                raise RuntimeError("Temporal returned a non-JSON workflow result")
            return TemporalWorkflowStatus(state=state, result=result)

        return cast(TemporalWorkflowStatus, _run_coroutine(operation))


class CommandReceiptStore(Protocol):
    """Durable receipt boundary for cross-dispatcher command idempotency."""

    def reserve(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: CommandIdentity,
        receipt: CommandReceipt,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Persist a queued receipt or return the existing matching receipt."""

    def reconcile_dispatched(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: CommandIdentity,
        receipt: CommandReceipt,
    ) -> CommandReceipt:
        """Persist a receipt after Temporal accepts its workflow."""

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID | None = None,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt | None:
        """Return a matching receipt without changing its state."""


class InMemoryCommandReceiptStore:
    """Thread-safe receipt store suitable for offline tests and local mode."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._receipts: dict[ReceiptKey, tuple[CommandIdentity, CommandReceipt]] = {}
        self._payloads: dict[ReceiptKey, str] = {}

    def reserve(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: CommandIdentity,
        receipt: CommandReceipt,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Persist a queued receipt, failing closed on an identity collision."""
        key = (tenant_id, idempotency_key)
        payload_text = json.dumps(
            dict(payload or {}), sort_keys=True, separators=(",", ":")
        )
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                if existing[0] != identity:
                    raise IdempotencyConflict()
                if self._payloads.get(key, "{}") != payload_text:
                    raise IdempotencyConflict()
                return existing[1]
            self._receipts[key] = (identity, receipt)
            self._payloads[key] = payload_text
            return receipt

    def reconcile_dispatched(
        self,
        *,
        tenant_id: UUID,
        idempotency_key: str,
        identity: CommandIdentity,
        receipt: CommandReceipt,
    ) -> CommandReceipt:
        """Advance a reserved receipt once while preserving its identity."""
        key = (tenant_id, idempotency_key)
        with self._lock:
            existing = self._receipts.get(key)
            if existing is None or existing[0] != identity:
                raise IdempotencyConflict()
            stored = existing[1]
            if (
                stored.command_id != receipt.command_id
                or stored.workflow_id != receipt.workflow_id
            ):
                raise IdempotencyConflict()
            if stored.state == "dispatched":
                return stored
            if stored.state != "queued" or receipt.state != "dispatched":
                raise IdempotencyConflict()
            self._receipts[key] = (identity, receipt)
            return receipt

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt | None:
        """Return a matching receipt without modifying its state."""
        with self._lock:
            existing = self._receipts.get((tenant_id, idempotency_key))
        if existing is None or existing[0] != (project_id, episode_id, command):
            return None
        if payload is not None:
            payload_text = json.dumps(
                dict(payload), sort_keys=True, separators=(",", ":")
            )
            if self._payloads.get((tenant_id, idempotency_key), "{}") != payload_text:
                raise IdempotencyConflict()
        return existing[1]


def workflow_id_for_command(
    *,
    tenant_id: UUID,
    project_id: UUID,
    episode_id: UUID,
    command: CommandName,
    idempotency_key: str,
) -> str:
    """Return a tenant-scoped workflow identity without exposing the key."""
    identity = {
        "tenant_id": str(tenant_id),
        "project_id": str(project_id),
        "episode_id": str(episode_id),
        "command": command,
        "idempotency_key": idempotency_key,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"poddown-command-{sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


class TemporalCommandDispatcher:
    """Submit or replay API commands through an injected Temporal port."""

    def __init__(
        self,
        transport: TemporalCommandTransport,
        *,
        task_queue: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        receipt_store: CommandReceiptStore | None = None,
    ) -> None:
        if not hasattr(transport, "start_workflow"):
            raise TypeError("transport must expose start_workflow(request)")
        if not isinstance(task_queue, str) or not task_queue.strip():
            raise ValueError("task_queue must be non-empty")
        self._transport = transport
        self._task_queue = task_queue
        self._clock = clock
        self._receipt_store = receipt_store or InMemoryCommandReceiptStore()

    def submit(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Start one workflow or replay its command identity."""
        identity = (project_id, episode_id, command)
        workflow_id = workflow_id_for_command(
            tenant_id=tenant_id,
            project_id=project_id,
            episode_id=episode_id,
            command=command,
            idempotency_key=idempotency_key,
        )
        receipt = self._receipt_store.reserve(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            identity=identity,
            receipt=CommandReceipt(
                command_id=uuid7(),
                episode_id=episode_id,
                idempotency_key=idempotency_key,
                command=command,
                workflow_id=workflow_id,
                state="queued",
                created_at=self._clock(),
            ),
            payload=payload,
        )
        if receipt.state != "queued":
            return receipt
        request = TemporalCommandRequest(
            workflow_id=workflow_id,
            task_queue=self._task_queue,
            payload=json.dumps(
                {
                    "tenant_id": str(tenant_id),
                    "project_id": str(project_id),
                    "episode_id": str(episode_id),
                    "command": command,
                    "payload": dict(payload or {}),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        try:
            self._transport.start_workflow(request)
        except TemporalWorkflowAlreadyStarted as error:
            if error.workflow_id != workflow_id:
                raise
        dispatched = receipt.model_copy(
            update={"state": "dispatched", "workflow_id": workflow_id}
        )
        return self._receipt_store.reconcile_dispatched(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            identity=identity,
            receipt=dispatched,
        )

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt | None:
        """Return a dispatched receipt or recover a queued workflow."""
        if project_id is None:
            raise ValueError("project_id is required for Temporal replay")
        receipt = self._receipt_store.replay(
            tenant_id=tenant_id,
            project_id=project_id,
            episode_id=episode_id,
            command=command,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if receipt is None or receipt.state == "dispatched":
            return receipt
        return self.submit(
            tenant_id=tenant_id,
            project_id=project_id,
            episode_id=episode_id,
            command=command,
            idempotency_key=idempotency_key,
            payload=payload,
        )


__all__ = [
    "CommandReceiptStore",
    "InMemoryCommandReceiptStore",
    "TemporalClientTransport",
    "TemporalCommandDispatcher",
    "TemporalCommandRequest",
    "TemporalCommandTransport",
    "TemporalWorkflowClient",
    "TemporalWorkflowHandle",
    "TemporalWorkflowState",
    "TemporalWorkflowStatus",
    "TemporalWorkflowStatusClient",
    "TemporalWorkflowAlreadyStarted",
    "workflow_id_for_command",
]
