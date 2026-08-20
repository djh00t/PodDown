"""BDD bindings for durable command job and publication approval seams."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.command_repository import CommandJobRepository, CommandReplayConflict
from poddown.publication_approval_repository import (
    ApprovalUnavailable,
    PublicationApprovalRepository,
)

scenarios("../features/command_repositories.feature")

TENANT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e1")
OTHER_TENANT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e2")
PROJECT_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e3")
EPISODE_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e4")
VERSION_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e5")
JOB_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e6")
OTHER_JOB_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e9")
PUBLICATION_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e7")
APPROVAL_ID = UUID("0198d59c-b420-70ce-ae46-786eeeb387e8")
NOW = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)
ACTOR_ID = "publisher@example.test"
NONCE_SHA256 = "c" * 64


def _connection_factory(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE render_jobs (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
            episode_id TEXT NOT NULL, episode_version_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            request_sha256 TEXT NOT NULL, status TEXT NOT NULL,
            attempt INTEGER NOT NULL, created_at TEXT NOT NULL,
            UNIQUE (tenant_id, workflow_id), UNIQUE (tenant_id, id, request_sha256)
        );
        CREATE TABLE publications (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
            episode_id TEXT NOT NULL
        );
        CREATE TABLE publication_approvals (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, publication_id TEXT NOT NULL,
            operation TEXT NOT NULL, approval_reference TEXT NOT NULL,
            actor_id TEXT NOT NULL, nonce_sha256 TEXT NOT NULL,
            approved_at TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT,
            created_at TEXT NOT NULL, UNIQUE (tenant_id, approval_reference)
        );
        """
    )
    connection.commit()


@pytest.fixture
def repository_context(tmp_path: Path) -> dict[str, object]:
    database = tmp_path / "repositories.sqlite3"
    connection = _connection_factory(database)
    _schema(connection)
    connection.close()
    return {"database": database}


@given("a persisted command job repository")
def persisted_command_job(repository_context: dict[str, object]) -> None:
    database = repository_context["database"]
    repository = CommandJobRepository(lambda: _connection_factory(database))
    repository_context["job"] = repository.record_or_replay(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        job_id=JOB_ID,
        workflow_id="workflow-1",
        content_sha256="a" * 64,
        request_sha256="b" * 64,
        created_at=NOW,
    )


@when("the command job repository is reconstructed")
def reconstruct_command_job_repository(repository_context: dict[str, object]) -> None:
    database = repository_context["database"]
    repository_context["repository"] = CommandJobRepository(
        lambda: _connection_factory(database)
    )


@then("the same command request returns its original workflow job")
def command_job_replays(repository_context: dict[str, object]) -> None:
    repository = repository_context["repository"]
    replayed = repository.record_or_replay(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        job_id=JOB_ID,
        workflow_id="workflow-1",
        content_sha256="a" * 64,
        request_sha256="b" * 64,
        created_at=NOW,
    )
    assert replayed == repository_context["job"]


@when("the command request is replayed with a different workflow identity")
def conflicting_command_replay(repository_context: dict[str, object]) -> None:
    database = repository_context["database"]
    repository = CommandJobRepository(lambda: _connection_factory(database))
    with pytest.raises(CommandReplayConflict):
        repository.record_or_replay(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            episode_version_id=VERSION_ID,
            job_id=JOB_ID,
            workflow_id="workflow-2",
            content_sha256="a" * 64,
            request_sha256="b" * 64,
            created_at=NOW,
        )


@then("the command replay fails safely")
def command_replay_fails_safely() -> None:
    assert True


@when("another tenant replays the command request")
def other_tenant_replays_command(repository_context: dict[str, object]) -> None:
    database = repository_context["database"]
    repository = CommandJobRepository(lambda: _connection_factory(database))
    repository_context["other_job"] = repository.record_or_replay(
        tenant_id=OTHER_TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        job_id=OTHER_JOB_ID,
        workflow_id="workflow-1",
        content_sha256="a" * 64,
        request_sha256="b" * 64,
        created_at=NOW,
    )


@then("the other tenant receives a distinct command job")
def other_tenant_receives_distinct_job(repository_context: dict[str, object]) -> None:
    assert repository_context["other_job"] != repository_context["job"]


@given("a persisted scoped publication approval")
def persisted_publication_approval(repository_context: dict[str, object]) -> None:
    database = repository_context["database"]
    connection = _connection_factory(database)
    connection.execute(
        "INSERT INTO publications (id, tenant_id, project_id, episode_id) "
        "VALUES (?, ?, ?, ?)",
        (str(PUBLICATION_ID), str(TENANT_ID), str(PROJECT_ID), str(EPISODE_ID)),
    )
    connection.commit()
    connection.close()
    repository = PublicationApprovalRepository(lambda: _connection_factory(database))
    repository.record(
        approval_id=APPROVAL_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        publication_id=PUBLICATION_ID,
        operation="publish",
        approval_reference="approval-1",
        actor_id=ACTOR_ID,
        nonce_sha256=NONCE_SHA256,
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    repository_context["approval_repository"] = repository


@when("the approval is consumed twice")
def consume_approval_twice(repository_context: dict[str, object]) -> None:
    repository = repository_context["approval_repository"]
    repository_context["consumed"] = repository.consume(
        approval_id=APPROVAL_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        publication_id=PUBLICATION_ID,
        operation="publish",
        actor_id=ACTOR_ID,
        nonce_sha256=NONCE_SHA256,
        consumed_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ApprovalUnavailable):
        repository.consume(
            approval_id=APPROVAL_ID,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            publication_id=PUBLICATION_ID,
            operation="publish",
            actor_id=ACTOR_ID,
            nonce_sha256=NONCE_SHA256,
            consumed_at=NOW + timedelta(minutes=2),
        )


@then("only the first approval consumption succeeds")
def only_first_consumption_succeeds(repository_context: dict[str, object]) -> None:
    assert repository_context["consumed"].consumed_at == NOW + timedelta(minutes=1)


@when("the command job transitions and the repository is reconstructed")
def transition_and_reconstruct_command_job(
    repository_context: dict[str, object],
) -> None:
    database = repository_context["database"]
    repository = CommandJobRepository(lambda: _connection_factory(database))
    repository.transition(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        job_id=JOB_ID,
        state="running",
    )
    repository_context["repository"] = CommandJobRepository(
        lambda: _connection_factory(database)
    )


@then("the replay returns the current command job state")
def replay_returns_current_command_job_state(
    repository_context: dict[str, object],
) -> None:
    repository = repository_context["repository"]
    replayed = repository.record_or_replay(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        job_id=JOB_ID,
        workflow_id="workflow-1",
        content_sha256="a" * 64,
        request_sha256="b" * 64,
        created_at=NOW,
    )
    assert replayed.state == "running"


@when("the approval is consumed with a different operation")
def consume_approval_with_different_operation(
    repository_context: dict[str, object],
) -> None:
    repository = repository_context["approval_repository"]
    with pytest.raises(ApprovalUnavailable):
        repository.consume(
            approval_id=APPROVAL_ID,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            publication_id=PUBLICATION_ID,
            operation="delete",
            actor_id=ACTOR_ID,
            nonce_sha256=NONCE_SHA256,
            consumed_at=NOW + timedelta(minutes=1),
        )


@given("an expired scoped publication approval")
def expired_scoped_publication_approval(repository_context: dict[str, object]) -> None:
    persisted_publication_approval(repository_context)
    repository_context["expired_at"] = NOW + timedelta(minutes=6)


@when("the approval is consumed with its complete evidence")
def consume_approval_with_complete_evidence(
    repository_context: dict[str, object],
) -> None:
    repository = repository_context["approval_repository"]
    with pytest.raises(ApprovalUnavailable):
        repository.consume(
            approval_id=APPROVAL_ID,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            publication_id=PUBLICATION_ID,
            operation="publish",
            actor_id=ACTOR_ID,
            nonce_sha256=NONCE_SHA256,
            consumed_at=repository_context.get(
                "expired_at", NOW + timedelta(minutes=1)
            ),
        )


@then("the approval is unavailable")
def approval_is_unavailable() -> None:
    assert True


@given("a publication approval repository")
def publication_approval_repository(repository_context: dict[str, object]) -> None:
    database = repository_context["database"]
    repository_context["approval_repository"] = PublicationApprovalRepository(
        lambda: _connection_factory(database)
    )


@when("malformed approval evidence is recorded")
def malformed_approval_evidence_is_recorded(
    repository_context: dict[str, object],
) -> None:
    repository = repository_context["approval_repository"]
    with pytest.raises(ValueError):
        repository.record(
            approval_id=uuid4(),
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            publication_id=PUBLICATION_ID,
            operation="publish",
            approval_reference="approval-1",
            actor_id=" ",
            nonce_sha256="C" * 64,
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )


@then("the malformed approval evidence is rejected")
def malformed_approval_evidence_is_rejected() -> None:
    assert True


@when("two callers consume the approval concurrently")
def consume_approval_concurrently(repository_context: dict[str, object]) -> None:
    database = repository_context["database"]

    def consume() -> bool:
        repository = PublicationApprovalRepository(
            lambda: _connection_factory(database)
        )
        try:
            repository.consume(
                approval_id=APPROVAL_ID,
                tenant_id=TENANT_ID,
                project_id=PROJECT_ID,
                episode_id=EPISODE_ID,
                publication_id=PUBLICATION_ID,
                operation="publish",
                actor_id=ACTOR_ID,
                nonce_sha256=NONCE_SHA256,
                consumed_at=NOW + timedelta(minutes=1),
            )
        except ApprovalUnavailable:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        repository_context["consumption_results"] = tuple(
            executor.map(lambda _: consume(), range(2))
        )


@then("exactly one approval consumption succeeds")
def exactly_one_approval_consumption_succeeds(
    repository_context: dict[str, object],
) -> None:
    results = repository_context["consumption_results"]
    assert results.count(True) == 1
