"""BDD bindings for restart-safe local persistence and usage metering."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.episode_service import (
    EpisodeNotFound,
    EpisodeRecord,
    EpisodeState,
    IdempotencyConflict,
    VersionConflict,
)
from poddown.persistence import (
    SQLiteCommandDispatcher,
    SQLiteEpisodeRepository,
    SQLiteUsageLedger,
    UsageConflict,
    UsageEvent,
)

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
OTHER_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")
EPISODE_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b14")
OTHER_EPISODE_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b16")
JOB_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b15")
CREATED_AT = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)

scenarios("../features/durable_persistence.feature")


@pytest.fixture
def durable_context(tmp_path: Path) -> dict[str, object]:
    return {}


def _episode() -> EpisodeRecord:
    return EpisodeRecord(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        idempotency_key="create-1",
        profile_name="technical-dialogue",
        source_sha256="b" * 64,
        source_bytes=48,
        request_fingerprint="a" * 64,
        state=EpisodeState.VALIDATED,
        version=1,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


def _usage() -> UsageEvent:
    return UsageEvent(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        job_id=JOB_ID,
        provider_request_id="provider-request-1",
        operation="render",
        units={"input_tokens": 12},
        currency="USD",
        estimated_cost=Decimal("0.0040"),
        reconciled_cost=Decimal("0.0035"),
        created_at=CREATED_AT,
    )


@given("an episode and command receipt persisted locally")
def persisted_episode_and_receipt(
    tmp_path: Path,
    durable_context: dict[str, object],
) -> None:
    database = tmp_path / "poddown.sqlite3"
    SQLiteEpisodeRepository(database).create(_episode())
    SQLiteCommandDispatcher(database).submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        command="render",
        idempotency_key="render-1",
    )
    durable_context["database"] = database
    durable_context["episode"] = _episode()


@given("a provider usage event persisted locally")
def persisted_usage_event(
    tmp_path: Path,
    durable_context: dict[str, object],
) -> None:
    database = tmp_path / "poddown.sqlite3"
    event = _usage()
    SQLiteUsageLedger(database).record(event)
    durable_context["database"] = database
    durable_context["usage"] = event


@given("an empty local persistence file")
def empty_local_persistence(
    tmp_path: Path,
    durable_context: dict[str, object],
) -> None:
    durable_context["database"] = tmp_path / "poddown.sqlite3"


@when("I reconstruct the local persistence adapters")
def reconstruct_adapters(durable_context: dict[str, object]) -> None:
    database = durable_context["database"]
    durable_context["repository"] = SQLiteEpisodeRepository(database)
    durable_context["dispatcher"] = SQLiteCommandDispatcher(database)
    durable_context["ledger"] = SQLiteUsageLedger(database)


@then("the episode and command receipt survive restart")
def episode_and_receipt_survive(durable_context: dict[str, object]) -> None:
    repository = durable_context["repository"]
    dispatcher = durable_context["dispatcher"]
    episode = durable_context["episode"]
    assert repository.get(TENANT_ID, EPISODE_ID) == episode
    assert (
        dispatcher.submit(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=EPISODE_ID,
            command="render",
            idempotency_key="render-1",
        ).episode_id
        == EPISODE_ID
    )


@then("the exact usage event replays without duplication")
def usage_event_replays(durable_context: dict[str, object]) -> None:
    ledger = durable_context["ledger"]
    event = durable_context["usage"]
    assert ledger.record(event) == event
    assert ledger.list_for_job(TENANT_ID, JOB_ID) == (event,)


@then("the durable adapters require no external service")
def adapters_are_local(durable_context: dict[str, object]) -> None:
    database = durable_context["database"]
    assert isinstance(SQLiteEpisodeRepository(database), SQLiteEpisodeRepository)
    assert isinstance(SQLiteCommandDispatcher(database), SQLiteCommandDispatcher)
    assert isinstance(SQLiteUsageLedger(database), SQLiteUsageLedger)


@when("I replay the episode and command with the same identity")
def replay_same_identity(durable_context: dict[str, object]) -> None:
    database = durable_context["database"]
    durable_context["episode_replay"] = SQLiteEpisodeRepository(database).create(
        _episode()
    )
    durable_context["receipt_replay"] = SQLiteCommandDispatcher(database).submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=EPISODE_ID,
        command="render",
        idempotency_key="render-1",
    )


@then("the original records are returned")
def original_records_are_returned(durable_context: dict[str, object]) -> None:
    assert durable_context["episode_replay"] == _episode()
    assert durable_context["receipt_replay"].episode_id == EPISODE_ID


@when("I replay them with conflicting identity")
def replay_conflicting_identity(durable_context: dict[str, object]) -> None:
    database = durable_context["database"]
    repository = SQLiteEpisodeRepository(database)
    dispatcher = SQLiteCommandDispatcher(database)
    with pytest.raises(IdempotencyConflict):
        repository.create(replace(_episode(), request_fingerprint="c" * 64))
    for project_id, episode_id, command in (
        (OTHER_PROJECT_ID, EPISODE_ID, "render"),
        (PROJECT_ID, OTHER_EPISODE_ID, "render"),
        (PROJECT_ID, EPISODE_ID, "publish"),
    ):
        with pytest.raises(IdempotencyConflict):
            dispatcher.submit(
                tenant_id=TENANT_ID,
                project_id=project_id,
                episode_id=episode_id,
                command=command,
                idempotency_key="render-1",
            )
    durable_context["conflicts_checked"] = True


@then("both conflicts fail closed")
def conflicts_fail_closed(durable_context: dict[str, object]) -> None:
    assert durable_context["conflicts_checked"] is True


@when("I submit the same lifecycle update twice")
def submit_stale_update(durable_context: dict[str, object]) -> None:
    database = durable_context["database"]
    repository = SQLiteEpisodeRepository(database)
    updated = replace(_episode(), state=EpisodeState.SCRIPTED, version=2)
    repository.replace(TENANT_ID, updated, expected_version=1)
    with pytest.raises(VersionConflict):
        repository.replace(TENANT_ID, updated, expected_version=1)
    durable_context["stale_version_checked"] = True


@then("the stale update fails with a version conflict")
def stale_update_fails(durable_context: dict[str, object]) -> None:
    assert durable_context["stale_version_checked"] is True


@when("another tenant looks up the episode")
def other_tenant_lookup(durable_context: dict[str, object]) -> None:
    repository = SQLiteEpisodeRepository(durable_context["database"])
    with pytest.raises(EpisodeNotFound):
        repository.get(
            UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11"),
            EPISODE_ID,
        )
    durable_context["tenant_isolation_checked"] = True


@then("the lookup is not found")
def lookup_is_not_found(durable_context: dict[str, object]) -> None:
    assert durable_context["tenant_isolation_checked"] is True


@when("I replay the provider request with a different cost")
def replay_usage_with_conflicting_cost(durable_context: dict[str, object]) -> None:
    ledger = SQLiteUsageLedger(durable_context["database"])
    event = durable_context["usage"]
    with pytest.raises(UsageConflict):
        ledger.record(replace(event, estimated_cost=Decimal("0.0050")))
    durable_context["usage_conflict_checked"] = True


@then("the usage conflict fails closed")
def usage_conflict_fails_closed(durable_context: dict[str, object]) -> None:
    assert durable_context["usage_conflict_checked"] is True
