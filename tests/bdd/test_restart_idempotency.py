"""BDD coverage for restart-safe deterministic-local production seams."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from poddown.api.models import CommandReceipt
from poddown.api.temporal_dispatcher import (
    TemporalCommandDispatcher,
    TemporalCommandRequest,
    TemporalWorkflowAlreadyStarted,
    workflow_id_for_command,
)
from poddown.audio import DeterministicLocalRenderer
from poddown.episode_service import IdempotencyConflict
from poddown.persistence import SQLiteTemporalCommandReceiptStore
from poddown.runtime import RuntimeDependencies, RuntimeSettings, compose_runtime

scenarios("../features/restart_idempotency.feature")

TENANT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
OTHER_PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"
PROFILE = "technical-dialogue"
SOURCE = "---\npoddown:\n  profile: technical-dialogue\n---\n# Restart fixture\n"


class RecordingTransport:
    """Deterministically record workflow starts without a Temporal service."""

    def __init__(self) -> None:
        self.requests: list[TemporalCommandRequest] = []
        self.accepted_workflow_ids: list[str] = []

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        """Record the supplied workflow request locally."""
        self.requests.append(request)
        self.accepted_workflow_ids.append(request.workflow_id)


class FailBeforeTemporalAcceptanceTransport(RecordingTransport):
    """Simulate a crash after reservation but before Temporal accepts render."""

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        self.requests.append(request)
        if '"command":"render"' in request.payload:
            raise RuntimeError("process stopped before Temporal workflow start")
        self.accepted_workflow_ids.append(request.workflow_id)


class AcceptThenCrashTransport(RecordingTransport):
    """Simulate a crash after Temporal accepts render but before reconciliation."""

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        super().start_workflow(request)
        if '"command":"render"' in request.payload:
            raise RuntimeError("process stopped after Temporal workflow start")


class AlreadyStartedTransport(RecordingTransport):
    """Simulate Temporal reporting an existing deterministic workflow identity."""

    def start_workflow(self, request: TemporalCommandRequest) -> None:
        self.requests.append(request)
        raise TemporalWorkflowAlreadyStarted(request.workflow_id)


def _headers(key: str, *, project_id: str = PROJECT_ID) -> dict[str, str]:
    return {
        "X-Tenant-ID": TENANT_ID,
        "X-Project-ID": project_id,
        "Idempotency-Key": key,
    }


def _components(database: Path, transport: RecordingTransport):
    dispatcher = TemporalCommandDispatcher(
        transport,
        task_queue="poddown-commands",
        receipt_store=SQLiteTemporalCommandReceiptStore(database),
    )
    return compose_runtime(
        RuntimeSettings(
            mode="deterministic-local",
            data_root=database.parent,
            database_path=database,
        ),
        RuntimeDependencies(
            renderer=DeterministicLocalRenderer(),
            dispatcher=dispatcher,
        ),
    )


@pytest.fixture
def restart_context() -> dict[str, object]:
    return {}


@given("a locally dispatched render command in packaged deterministic-local runtime")
def locally_dispatched_render_command(
    tmp_path: Path, restart_context: dict[str, object]
) -> None:
    database = tmp_path / "poddown.sqlite3"
    transport = RecordingTransport()
    components = _components(database, transport)
    with TestClient(components.app) as client:
        created = client.post(
            "/v1/episodes",
            headers=_headers("restart-create-001"),
            json={"source": SOURCE, "profile": PROFILE},
        )
        assert created.status_code == 202
        episode_id = created.json()["episode"]["id"]
        rendered = client.post(
            f"/v1/episodes/{episode_id}/render",
            headers=_headers("restart-render-001"),
        )
        status = client.get(
            f"/v1/episodes/{episode_id}/status",
            headers=_headers("restart-status-001"),
        )
    assert rendered.status_code == 202
    assert status.status_code == 200
    restart_context.update(
        database=database,
        episode_id=episode_id,
        first_receipt=rendered.json(),
        first_status=status.json(),
        first_transport=transport,
        first_worker_contracts=(
            components.worker_workflows,
            components.worker_activity_names,
        ),
    )


@when(
    "I reconstruct the packaged runtime from the same local state and replay "
    "the render command"
)
def reconstruct_and_replay(restart_context: dict[str, object]) -> None:
    transport = RecordingTransport()
    database = restart_context["database"]
    episode_id = restart_context["episode_id"]
    assert isinstance(database, Path)
    assert isinstance(episode_id, str)
    components = _components(database, transport)
    with TestClient(components.app) as client:
        restart_context["replay"] = client.post(
            f"/v1/episodes/{episode_id}/render",
            headers=_headers("restart-render-001"),
        )
        restart_context["status"] = client.get(
            f"/v1/episodes/{episode_id}/status",
            headers=_headers("restart-status-002"),
        )
        with pytest.raises(IdempotencyConflict):
            TemporalCommandDispatcher(
                transport,
                task_queue="poddown-commands",
                receipt_store=SQLiteTemporalCommandReceiptStore(database),
            ).submit(
                tenant_id=UUID(TENANT_ID),
                project_id=UUID(OTHER_PROJECT_ID),
                episode_id=UUID(episode_id),
                command="render",
                idempotency_key="restart-render-001",
            )
    restart_context["restart_transport"] = transport
    restart_context["worker_contracts"] = (
        components.worker_workflows,
        components.worker_activity_names,
    )


@then(
    "the original dispatched receipt and workflow identity are returned without "
    "another workflow start"
)
def original_receipt_replayed(restart_context: dict[str, object]) -> None:
    replay = restart_context["replay"]
    transport = restart_context["restart_transport"]
    assert replay.status_code == 202
    assert replay.json() == restart_context["first_receipt"]
    assert replay.json()["workflow_id"]
    assert transport.requests == []


@then("the authoritative episode status remains available after restart")
def authoritative_status_remains_available(restart_context: dict[str, object]) -> None:
    status = restart_context["status"]
    assert status.status_code == 200
    assert status.json() == restart_context["first_status"]


@then("a conflicting render identity is rejected without another workflow start")
def conflicting_identity_is_rejected(restart_context: dict[str, object]) -> None:
    transport = restart_context["restart_transport"]
    assert transport.requests == []


@then("the local worker contract set survives reconstruction")
def worker_contract_set_survives(restart_context: dict[str, object]) -> None:
    assert (
        restart_context["worker_contracts"] == restart_context["first_worker_contracts"]
    )


@given("a queued render reservation survives a crash before Temporal accepts it")
def queued_reservation_survives_crash_before_temporal_acceptance(
    tmp_path: Path, restart_context: dict[str, object]
) -> None:
    database = tmp_path / "queued-reservation.sqlite3"
    transport = FailBeforeTemporalAcceptanceTransport()
    components = _components(database, transport)
    with TestClient(components.app) as client:
        created = client.post(
            "/v1/episodes",
            headers=_headers("restart-queued-create-001"),
            json={"source": SOURCE, "profile": PROFILE},
        )
        assert created.status_code == 202
        episode_id = created.json()["episode"]["id"]
        with pytest.raises(RuntimeError, match="process stopped"):
            client.post(
                f"/v1/episodes/{episode_id}/render",
                headers=_headers("restart-queued-001"),
            )

    queued = SQLiteTemporalCommandReceiptStore(database).replay(
        tenant_id=UUID(TENANT_ID),
        project_id=UUID(PROJECT_ID),
        episode_id=UUID(episode_id),
        command="render",
        idempotency_key="restart-queued-001",
    )
    assert queued is not None
    assert queued.state == "queued"
    restart_context.update(
        queued_database=database,
        queued_episode_id=episode_id,
        queued_receipt=queued,
        failed_start_transport=transport,
    )


@when("I restart and Temporal accepts the queued render workflow")
def restart_and_temporal_accepts_queued_render_workflow(
    restart_context: dict[str, object],
) -> None:
    database = restart_context["queued_database"]
    episode_id = restart_context["queued_episode_id"]
    assert isinstance(database, Path)
    assert isinstance(episode_id, str)
    transport = RecordingTransport()
    components = _components(database, transport)
    with TestClient(components.app) as client:
        response = client.post(
            f"/v1/episodes/{episode_id}/render",
            headers=_headers("restart-queued-001"),
        )
    restart_context.update(recovery_response=response, recovery_transport=transport)


@then(
    "the queued render receives two attempts and one accepted stable workflow identity"
)
def queued_render_receives_two_attempts_and_one_accepted_stable_workflow_identity(
    restart_context: dict[str, object],
) -> None:
    queued = restart_context["queued_receipt"]
    failed_transport = restart_context["failed_start_transport"]
    transport = restart_context["recovery_transport"]
    response = restart_context["recovery_response"]
    assert response.status_code == 202
    failed_render_requests = [
        request
        for request in failed_transport.requests
        if '"command":"render"' in request.payload
    ]
    recovery_render_requests = [
        request
        for request in transport.requests
        if '"command":"render"' in request.payload
    ]
    attempted_workflow_ids = [
        request.workflow_id
        for request in failed_render_requests + recovery_render_requests
    ]
    accepted_workflow_ids = [
        workflow_id
        for workflow_id in (
            failed_transport.accepted_workflow_ids + transport.accepted_workflow_ids
        )
        if workflow_id == queued.workflow_id
    ]
    assert attempted_workflow_ids == [queued.workflow_id, queued.workflow_id]
    assert accepted_workflow_ids == [queued.workflow_id]


@then("the already-started queued receipt is reconciled as dispatched")
def queued_receipt_is_reconciled_as_dispatched(
    restart_context: dict[str, object],
) -> None:
    queued = restart_context["queued_receipt"]
    response = restart_context["recovery_response"]
    assert response.json()["state"] == "dispatched"
    assert response.json()["command_id"] == str(queued.command_id)
    assert response.json()["workflow_id"] == queued.workflow_id


@given("a queued render reservation survives a crash after Temporal accepts it")
def queued_reservation_survives_crash_after_temporal_acceptance(
    tmp_path: Path, restart_context: dict[str, object]
) -> None:
    database = tmp_path / "queued-accepted-reservation.sqlite3"
    transport = AcceptThenCrashTransport()
    components = _components(database, transport)
    with TestClient(components.app) as client:
        created = client.post(
            "/v1/episodes",
            headers=_headers("restart-accepted-create-001"),
            json={"source": SOURCE, "profile": PROFILE},
        )
        assert created.status_code == 202
        episode_id = created.json()["episode"]["id"]
        with pytest.raises(RuntimeError, match="after Temporal workflow start"):
            client.post(
                f"/v1/episodes/{episode_id}/render",
                headers=_headers("restart-accepted-001"),
            )

    queued = SQLiteTemporalCommandReceiptStore(database).replay(
        tenant_id=UUID(TENANT_ID),
        project_id=UUID(PROJECT_ID),
        episode_id=UUID(episode_id),
        command="render",
        idempotency_key="restart-accepted-001",
    )
    assert queued is not None
    assert queued.state == "queued"
    restart_context.update(
        queued_database=database,
        queued_episode_id=episode_id,
        queued_receipt=queued,
        accepted_start_transport=transport,
    )


@when("I restart and Temporal reports the queued render workflow already started")
def restart_and_temporal_reports_queued_render_workflow_already_started(
    restart_context: dict[str, object],
) -> None:
    database = restart_context["queued_database"]
    episode_id = restart_context["queued_episode_id"]
    assert isinstance(database, Path)
    assert isinstance(episode_id, str)
    transport = AlreadyStartedTransport()
    components = _components(database, transport)
    with TestClient(components.app) as client:
        response = client.post(
            f"/v1/episodes/{episode_id}/render",
            headers=_headers("restart-accepted-001"),
        )
    restart_context.update(recovery_response=response, recovery_transport=transport)


@then("recovery reconciles the accepted render without a second accepted start")
def recovery_reconciles_accepted_render_without_second_accepted_start(
    restart_context: dict[str, object],
) -> None:
    queued = restart_context["queued_receipt"]
    accepted_start_transport = restart_context["accepted_start_transport"]
    recovery_transport = restart_context["recovery_transport"]
    response = restart_context["recovery_response"]
    assert response.status_code == 202
    assert isinstance(accepted_start_transport, RecordingTransport)
    assert isinstance(recovery_transport, RecordingTransport)
    attempted_workflow_ids = [
        request.workflow_id
        for request in accepted_start_transport.requests + recovery_transport.requests
        if '"command":"render"' in request.payload
    ]
    accepted_workflow_ids = [
        workflow_id
        for workflow_id in (
            accepted_start_transport.accepted_workflow_ids
            + recovery_transport.accepted_workflow_ids
        )
        if workflow_id == queued.workflow_id
    ]
    assert attempted_workflow_ids == [queued.workflow_id, queued.workflow_id]
    assert accepted_workflow_ids == [queued.workflow_id]
    assert response.json()["state"] == "dispatched"
    assert response.json()["command_id"] == str(queued.command_id)
    assert response.json()["workflow_id"] == queued.workflow_id


@given("a queued durable render receipt has a tampered workflow identity")
def queued_durable_render_receipt_has_a_tampered_workflow_identity(
    tmp_path: Path, restart_context: dict[str, object]
) -> None:
    database = tmp_path / "tampered-workflow-receipt.sqlite3"
    episode_id = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")
    expected_workflow_id = workflow_id_for_command(
        tenant_id=UUID(TENANT_ID),
        project_id=UUID(PROJECT_ID),
        episode_id=episode_id,
        command="render",
        idempotency_key="tampered-workflow-001",
    )
    receipt_store = SQLiteTemporalCommandReceiptStore(database)
    receipt_store.reserve(
        tenant_id=UUID(TENANT_ID),
        idempotency_key="tampered-workflow-001",
        identity=(UUID(PROJECT_ID), episode_id, "render"),
        receipt=CommandReceipt(
            command_id=uuid4(),
            episode_id=episode_id,
            idempotency_key="tampered-workflow-001",
            command="render",
            workflow_id=expected_workflow_id,
            created_at=datetime.now(UTC),
        ),
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """UPDATE temporal_command_receipts SET workflow_id = ?
               WHERE tenant_id = ? AND idempotency_key = ?""",
            ("tampered-workflow-id", TENANT_ID, "tampered-workflow-001"),
        )
        connection.commit()
    finally:
        connection.close()
    restart_context.update(
        tampered_database=database,
        tampered_episode_id=episode_id,
        tampered_workflow_id="tampered-workflow-id",
    )


@when("I submit the matching render command through a reconstructed dispatcher")
def submit_matching_render_command_through_a_reconstructed_dispatcher(
    restart_context: dict[str, object],
) -> None:
    database = restart_context["tampered_database"]
    episode_id = restart_context["tampered_episode_id"]
    assert isinstance(database, Path)
    assert isinstance(episode_id, UUID)
    transport = RecordingTransport()
    with pytest.raises(IdempotencyConflict):
        TemporalCommandDispatcher(
            transport,
            task_queue="poddown-commands",
            receipt_store=SQLiteTemporalCommandReceiptStore(database),
        ).submit(
            tenant_id=UUID(TENANT_ID),
            project_id=UUID(PROJECT_ID),
            episode_id=episode_id,
            command="render",
            idempotency_key="tampered-workflow-001",
        )
    restart_context["tampered_transport"] = transport


@then("the tampered receipt is rejected without a workflow start or state transition")
def tampered_receipt_is_rejected_without_a_workflow_start_or_state_transition(
    restart_context: dict[str, object],
) -> None:
    database = restart_context["tampered_database"]
    episode_id = restart_context["tampered_episode_id"]
    transport = restart_context["tampered_transport"]
    assert isinstance(database, Path)
    assert isinstance(episode_id, UUID)
    assert isinstance(transport, RecordingTransport)
    stored_receipt = SQLiteTemporalCommandReceiptStore(database).replay(
        tenant_id=UUID(TENANT_ID),
        project_id=UUID(PROJECT_ID),
        episode_id=episode_id,
        command="render",
        idempotency_key="tampered-workflow-001",
    )
    assert transport.requests == []
    assert stored_receipt is not None
    assert stored_receipt.state == "queued"
    assert stored_receipt.workflow_id == restart_context["tampered_workflow_id"]


@given("a completed package candidate, a failing commit, and an incomplete candidate")
def package_candidates(tmp_path: Path, restart_context: dict[str, object]) -> None:
    from poddown.audio.production_workflow import (
        MasterAndFinalMasterQaResult,
        PackageCompletionError,
        PackageObjectReference,
        package_completion_for,
    )

    qa_result = MasterAndFinalMasterQaResult(
        episode_id="episode-1",
        episode_version_id="version-1",
        master_wav_checksum="a" * 64,
        master_mp3_checksum="b" * 64,
        qa_master_checksum="a" * 64,
        qa_passed=True,
        qa_critical_token_accuracy=1.0,
    )
    completion = package_completion_for(
        qa_result,
        wav_object=PackageObjectReference(
            "episode.wav", "a" * 64, "wav", 1, "audio/wav", "1.0"
        ),
        mp3_object=PackageObjectReference(
            "episode.mp3", "b" * 64, "mp3", 1, "audio/mpeg", "1.0"
        ),
        manifest_object=PackageObjectReference(
            "package-manifest.json",
            "c" * 64,
            "manifest",
            1,
            "application/json",
            "1.0",
        ),
    )
    restart_context.update(
        completion=completion,
        completed_database=tmp_path / "completed-package.sqlite3",
        failing_database=tmp_path / "failing-package.sqlite3",
        incomplete_database=tmp_path / "incomplete-package.sqlite3",
        package_error_type=PackageCompletionError,
    )


@when(
    "I replay the completed package, commit the failing package, and reject the "
    "incomplete candidate"
)
def replay_and_fail_package_completion(restart_context: dict[str, object]) -> None:
    from poddown.audio.production_workflow import package_completion_for
    from poddown.persistence import SQLitePackageCompletionRepository

    completion = restart_context["completion"]
    completed_database = restart_context["completed_database"]
    failing_database = restart_context["failing_database"]
    assert isinstance(completed_database, Path)
    assert isinstance(failing_database, Path)
    completed_repository = SQLitePackageCompletionRepository(completed_database)
    restart_context["first_package"] = completed_repository.commit_package_completion(
        completion
    )
    completed_repository = SQLitePackageCompletionRepository(completed_database)
    restart_context["replayed_package"] = (
        completed_repository.commit_package_completion(completion)
    )
    failing_repository = SQLitePackageCompletionRepository(
        failing_database, fail_next_commit=True
    )
    error_type = restart_context["package_error_type"]
    assert isinstance(error_type, type)
    with pytest.raises(error_type) as error:
        failing_repository.commit_package_completion(completion)
    restart_context["package_error"] = error.value
    reconstructed_failing_repository = SQLitePackageCompletionRepository(
        failing_database
    )
    restart_context["failed_completion_after_reconstruction"] = (
        reconstructed_failing_repository.get("episode-1", "version-1")
    )
    restart_context["retried_package"] = (
        reconstructed_failing_repository.commit_package_completion(completion)
    )
    restart_context["retried_package_replay"] = SQLitePackageCompletionRepository(
        failing_database
    ).commit_package_completion(completion)
    incomplete_database = restart_context["incomplete_database"]
    assert isinstance(incomplete_database, Path)
    with pytest.raises(error_type) as incomplete_error:
        package_completion_for(
            completion.final_master_qa,
            wav_object=completion.wav_object,
            mp3_object=completion.mp3_object,
            manifest_object=None,  # type: ignore[arg-type]
        )
    restart_context["incomplete_package_error"] = incomplete_error.value
    restart_context["fresh_incomplete_repository"] = SQLitePackageCompletionRepository(
        incomplete_database
    )


@then("the completed package is replayed unchanged")
def completed_package_replayed(restart_context: dict[str, object]) -> None:
    assert restart_context["first_package"] == restart_context["replayed_package"]


@then(
    "the failed tentative package commit has no completed record after reconstruction"
)
def failed_tentative_package_commit_has_no_completed_record_after_reconstruction(
    restart_context: dict[str, object],
) -> None:
    assert restart_context["failed_completion_after_reconstruction"] is None


@then("the package completion retry is replayed unchanged")
def package_completion_retry_is_replayed_unchanged(
    restart_context: dict[str, object],
) -> None:
    assert (
        restart_context["retried_package"] == restart_context["retried_package_replay"]
    )


@then("the incomplete package candidate has no completed record in a fresh repository")
def incomplete_package_candidate_has_no_completed_record(
    restart_context: dict[str, object],
) -> None:
    error_type = restart_context["package_error_type"]
    repository = restart_context["fresh_incomplete_repository"]
    assert isinstance(error_type, type)
    assert isinstance(restart_context["incomplete_package_error"], error_type)
    assert repository.get("episode-1", "version-1") is None
