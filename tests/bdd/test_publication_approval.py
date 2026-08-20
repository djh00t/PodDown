"""BDD and unit coverage for durable, one-time publication approvals."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.publication_approval import (
    PublicationApproval,
    PublicationApprovalConsumed,
    PublicationApprovalError,
    PublicationApprovalExpired,
    PublicationApprovalMismatch,
    PublicationOperation,
    SQLitePublicationApprovalRepository,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
EPISODE_ID = UUID("0190b8f7-5d93-7e31-8a3f-c8d7f0d6a901")
APPROVAL_ID = UUID("0190b8f7-5d93-7e30-8a3f-c8d7f0d6a900")
NONCE = "approval-nonce"
NONCE_SHA256 = "77feef004cfd54bb56ff372d9f710033fb2b0193a0223198afca449138aa0384"
ISSUED_AT = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=5)
MISMATCH_MESSAGE = "publication approval does not match request"

scenarios("../features/publication_approval.feature")


@pytest.fixture
def approval_context(tmp_path: Path) -> dict[str, object]:
    return {"database": tmp_path / "publication-approvals.sqlite3"}


def _approval(**overrides: object) -> PublicationApproval:
    values: dict[str, object] = {
        "approval_id": APPROVAL_ID,
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "operation": "publish",
        "actor_id": "actor-1",
        "nonce_sha256": NONCE_SHA256,
        "issued_at": ISSUED_AT,
        "expires_at": EXPIRES_AT,
        "consumed_at": None,
    }
    values.update(overrides)
    return PublicationApproval(**values)  # type: ignore[arg-type]


def _repository(
    tmp_path: Path, approval: PublicationApproval | None = None
) -> SQLitePublicationApprovalRepository:
    database = (
        tmp_path if tmp_path.suffix == ".sqlite3" else tmp_path / "approvals.sqlite3"
    )
    repository = SQLitePublicationApprovalRepository(database)
    repository.record(approval or _approval())
    return repository


def _consume(
    repository: SQLitePublicationApprovalRepository,
    *,
    tenant_id: UUID = TENANT_ID,
    project_id: UUID = PROJECT_ID,
    episode_id: UUID = EPISODE_ID,
    operation: str = "publish",
    actor_id: str = "actor-1",
    nonce: str = NONCE,
    occurred_at: datetime = ISSUED_AT + timedelta(minutes=1),
) -> PublicationApproval:
    return repository.consume(
        approval_id=APPROVAL_ID,
        tenant_id=tenant_id,
        project_id=project_id,
        episode_id=episode_id,
        operation=cast(PublicationOperation, operation),
        actor_id=actor_id,
        nonce=nonce,
        occurred_at=occurred_at,
    )


def _expect_mismatch(
    repository: SQLitePublicationApprovalRepository, **overrides: object
) -> None:
    arguments: dict[str, object] = {
        "approval_id": APPROVAL_ID,
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "operation": "publish",
        "actor_id": "actor-1",
        "nonce": NONCE,
        "occurred_at": ISSUED_AT + timedelta(minutes=1),
    }
    arguments.update(overrides)
    with pytest.raises(PublicationApprovalMismatch, match=MISMATCH_MESSAGE):
        repository.consume(**arguments)  # type: ignore[arg-type]


@given("an unconsumed publication approval")
def unconsumed_approval(approval_context: dict[str, object]) -> None:
    database = cast(Path, approval_context["database"])
    repository = _repository(database)
    approval_context.update(repository=repository, approval=_approval())


@given("a consumed publication approval")
def consumed_approval(approval_context: dict[str, object]) -> None:
    unconsumed_approval(approval_context)
    repository = cast(
        SQLitePublicationApprovalRepository, approval_context["repository"]
    )
    approval_context["consumed"] = _consume(repository)


@given("an expired publication approval")
def expired_approval(approval_context: dict[str, object]) -> None:
    database = cast(Path, approval_context["database"])
    repository = _repository(
        database,
        _approval(expires_at=ISSUED_AT + timedelta(minutes=2)),
    )
    approval_context.update(repository=repository, approval=_approval())


@when("I consume it for its exact scope and publish operation")
def consume_exact_scope(approval_context: dict[str, object]) -> None:
    repository = cast(
        SQLitePublicationApprovalRepository, approval_context["repository"]
    )
    approval_context["consumed"] = _consume(repository)


@then("the approval is marked consumed without storing its raw nonce")
def approval_consumed_without_raw_nonce(approval_context: dict[str, object]) -> None:
    consumed = cast(PublicationApproval, approval_context["consumed"])
    assert consumed.consumed_at == ISSUED_AT + timedelta(minutes=1)
    database = cast(Path, approval_context["database"])
    assert NONCE.encode() not in database.read_bytes()


@then("a second consume attempt is rejected")
def replay_is_rejected(approval_context: dict[str, object]) -> None:
    repository = cast(
        SQLitePublicationApprovalRepository, approval_context["repository"]
    )
    with pytest.raises(PublicationApprovalConsumed):
        _consume(repository, occurred_at=ISSUED_AT + timedelta(minutes=2))


@when("I consume it with a different project scope")
def consume_different_project(approval_context: dict[str, object]) -> None:
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        project_id=UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"),
    )


@when("I consume it with a different episode scope")
def consume_different_episode(approval_context: dict[str, object]) -> None:
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        episode_id=UUID("0190b8f7-5d93-7e32-8a3f-c8d7f0d6a902"),
    )


@when("I consume it with a different actor")
def consume_different_actor(approval_context: dict[str, object]) -> None:
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        actor_id="actor-2",
    )


@when("I consume it with a different nonce")
def consume_different_nonce(approval_context: dict[str, object]) -> None:
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        nonce="different-nonce",
    )


@when("I consume it with a different tenant scope")
def consume_different_tenant(approval_context: dict[str, object]) -> None:
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        tenant_id=UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
    )


@when("I consume it for the update operation")
def consume_update_operation(approval_context: dict[str, object]) -> None:
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        operation="update",
    )


@then("the consume request is rejected")
def consume_request_rejected() -> None:
    pass


@when("I retry it with a different tenant scope")
def retry_with_different_scope(approval_context: dict[str, object]) -> None:
    consume_different_tenant(approval_context)


@when("I consume it after expiry with a different tenant scope")
def consume_expiry_with_different_scope(approval_context: dict[str, object]) -> None:
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        tenant_id=UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
        occurred_at=ISSUED_AT + timedelta(minutes=6),
    )


@then("the mismatch does not reveal consumed or expired state")
def mismatch_does_not_reveal_state() -> None:
    pass


@when(
    "I consume it with a different tenant scope after corrupting persisted "
    "lifecycle data"
)
def consume_different_scope_with_malformed_lifecycle(
    approval_context: dict[str, object],
) -> None:
    database = cast(Path, approval_context["database"])
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE publication_approvals SET issued_at = ? WHERE approval_id = ?",
            ("not-a-date", str(APPROVAL_ID)),
        )
        connection.commit()
    finally:
        connection.close()
    _expect_mismatch(
        cast(SQLitePublicationApprovalRepository, approval_context["repository"]),
        tenant_id=UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
    )


@then("the mismatch does not reveal malformed lifecycle state")
def mismatch_does_not_reveal_malformed_lifecycle_state() -> None:
    pass


@when("I consume it before its issue time")
def consume_before_issue(approval_context: dict[str, object]) -> None:
    repository = cast(
        SQLitePublicationApprovalRepository, approval_context["repository"]
    )
    with pytest.raises(PublicationApprovalExpired):
        _consume(repository, occurred_at=ISSUED_AT - timedelta(seconds=1))


@when("I consume it after its expiry time")
def consume_after_expiry(approval_context: dict[str, object]) -> None:
    repository = cast(
        SQLitePublicationApprovalRepository, approval_context["repository"]
    )
    with pytest.raises(PublicationApprovalExpired):
        _consume(repository, occurred_at=ISSUED_AT + timedelta(minutes=6))


@then("the approval validity is rejected")
def approval_validity_rejected() -> None:
    pass


@when("I create an approval with an invalid operation")
def invalid_operation() -> None:
    with pytest.raises(PublicationApprovalError):
        _approval(operation=[])


@when("I create an approval with malformed runtime UUID values")
def malformed_runtime_uuid_values() -> None:
    for field in ("approval_id", "tenant_id", "project_id", "episode_id"):
        with pytest.raises(PublicationApprovalError):
            _approval(**{field: "not-a-uuid"})


@then("publication approval validation is rejected")
def publication_approval_validation_rejected() -> None:
    pass


@when("I restart the repository and consume the approval")
def restart_and_consume(approval_context: dict[str, object]) -> None:
    database = cast(Path, approval_context["database"])
    repository = SQLitePublicationApprovalRepository(database)
    approval_context["consumed"] = _consume(repository)


@then("the consumed state survives a repository restart")
def consumed_state_survives_restart(approval_context: dict[str, object]) -> None:
    database = cast(Path, approval_context["database"])
    repository = SQLitePublicationApprovalRepository(database)
    with pytest.raises(PublicationApprovalConsumed):
        _consume(repository, occurred_at=ISSUED_AT + timedelta(minutes=2))


@when("two consumers race to consume the approval")
def concurrent_consumers(approval_context: dict[str, object]) -> None:
    repository = cast(
        SQLitePublicationApprovalRepository, approval_context["repository"]
    )

    def consume() -> PublicationApproval:
        return _consume(repository)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: _run_consumer(consume), range(2)))
    approval_context["concurrent_outcomes"] = outcomes


def _run_consumer(
    consume: Callable[[], PublicationApproval],
) -> PublicationApproval | BaseException:
    try:
        return consume()
    except BaseException as error:
        return error


@then("exactly one concurrent consumer succeeds")
def exactly_one_concurrent_consumer_succeeds(
    approval_context: dict[str, object],
) -> None:
    outcomes = cast(
        list[PublicationApproval | BaseException],
        approval_context["concurrent_outcomes"],
    )
    assert sum(isinstance(outcome, PublicationApproval) for outcome in outcomes) == 1
    assert (
        sum(isinstance(outcome, PublicationApprovalConsumed) for outcome in outcomes)
        == 1
    )


@pytest.mark.parametrize(
    "field",
    ["approval_id", "tenant_id", "project_id", "episode_id"],
)
def test_model_rejects_non_uuid7_ids(field: str) -> None:
    arguments: dict[str, object] = {field: UUID("018f3c7d-9d04-4c25-8e20-9e8e0c4d3b14")}
    with pytest.raises(PublicationApprovalError):
        _approval(**arguments)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approval_id", "not-a-uuid"),
        ("tenant_id", []),
        ("project_id", object()),
        ("episode_id", "not-a-uuid"),
        ("actor_id", []),
        ("nonce_sha256", None),
        ("operation", []),
        ("issued_at", "not-a-date"),
        ("expires_at", []),
        ("consumed_at", "not-a-date"),
    ],
)
def test_model_maps_malformed_runtime_values_to_approval_error(
    field: str, value: object
) -> None:
    with pytest.raises(PublicationApprovalError):
        _approval(**{field: value})


def test_model_normalizes_hostile_datetime_subclasses_before_comparison() -> None:
    """Keep datetime subclass comparison behavior outside the approval contract."""

    class HostileDatetime(datetime):
        def astimezone(self, tz: object = None) -> datetime:
            return self

        def __le__(self, other: object) -> bool:
            raise TypeError("unexpected datetime comparison")

    hostile_expires_at = HostileDatetime(2026, 8, 12, 4, 5, tzinfo=UTC)
    approval = _approval(expires_at=hostile_expires_at)

    assert type(approval.expires_at) is datetime
    assert approval.expires_at == EXPIRES_AT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approval_id", "not-a-uuid"),
        ("tenant_id", []),
        ("project_id", object()),
        ("episode_id", "not-a-uuid"),
        ("operation", []),
        ("actor_id", []),
        ("nonce", None),
        ("occurred_at", "not-a-date"),
    ],
)
def test_consume_maps_malformed_runtime_values_to_approval_error(
    tmp_path: Path, field: str, value: object
) -> None:
    repository = _repository(tmp_path)
    arguments: dict[str, object] = {
        "approval_id": APPROVAL_ID,
        "tenant_id": TENANT_ID,
        "project_id": PROJECT_ID,
        "episode_id": EPISODE_ID,
        "operation": "publish",
        "actor_id": "actor-1",
        "nonce": NONCE,
        "occurred_at": ISSUED_AT + timedelta(minutes=1),
    }
    arguments[field] = value
    with pytest.raises(PublicationApprovalError):
        repository.consume(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "project_id", "episode_id", "actor_id", "nonce"],
)
def test_mismatch_is_generic_before_consumed_or_expired_state(
    tmp_path: Path, field: str
) -> None:
    repository = _repository(tmp_path)
    _consume(repository)
    mismatch: dict[str, object] = {
        "tenant_id": UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
        "project_id": UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"),
        "episode_id": UUID("0190b8f7-5d93-7e32-8a3f-c8d7f0d6a902"),
        "actor_id": "actor-2",
        "nonce": "different-nonce",
    }
    _expect_mismatch(repository, **{field: mismatch[field]})

    expired_repository = _repository(
        tmp_path / "expired",
        _approval(expires_at=ISSUED_AT + timedelta(minutes=2)),
    )
    _expect_mismatch(
        expired_repository,
        **{field: mismatch[field]},
        occurred_at=ISSUED_AT + timedelta(minutes=6),
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("tenant_id", "not-a-uuid"),
        ("project_id", "not-a-uuid"),
        ("episode_id", "not-a-uuid"),
        ("operation", "replace"),
        ("actor_id", 123),
        ("nonce_sha256", "not-a-hash"),
        ("issued_at", "not-a-date"),
        ("expires_at", "not-a-date"),
        ("consumed_at", "not-a-date"),
    ],
)
def test_malformed_repository_values_map_to_approval_error(
    tmp_path: Path, column: str, value: object
) -> None:
    repository = _repository(tmp_path)
    database = tmp_path / "approvals.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            f"UPDATE publication_approvals SET {column} = ? WHERE approval_id = ?",
            (value, str(APPROVAL_ID)),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PublicationApprovalError):
        _consume(repository)


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "project_id", "episode_id", "operation", "actor_id", "nonce"],
)
def test_mismatch_precedes_malformed_persisted_lifecycle_state(
    tmp_path: Path, field: str
) -> None:
    """Reject a mismatched request without parsing persisted lifecycle fields."""
    repository = _repository(tmp_path)
    database = tmp_path / "approvals.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE publication_approvals SET issued_at = ? WHERE approval_id = ?",
            ("not-a-date", str(APPROVAL_ID)),
        )
        connection.commit()
    finally:
        connection.close()
    mismatch: dict[str, object] = {
        "tenant_id": UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
        "project_id": UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"),
        "episode_id": UUID("0190b8f7-5d93-7e32-8a3f-c8d7f0d6a902"),
        "operation": "update",
        "actor_id": "actor-2",
        "nonce": "different-nonce",
    }

    _expect_mismatch(repository, **{field: mismatch[field]})


def test_repository_rejects_invalid_runtime_values(tmp_path: Path) -> None:
    with pytest.raises(PublicationApprovalError):
        SQLitePublicationApprovalRepository([])  # type: ignore[arg-type]
    repository = SQLitePublicationApprovalRepository(tmp_path / "approvals.sqlite3")
    with pytest.raises(PublicationApprovalError):
        repository.record(cast(PublicationApproval, object()))
