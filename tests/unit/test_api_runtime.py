"""Unit contracts for command receipt idempotency scope."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest

from poddown.api.runtime import InMemoryCommandDispatcher
from poddown.episode_service import IdempotencyConflict

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
FIRST_EPISODE_ID = UUID("01986e76-4ec6-7a8f-8000-000000000001")
SECOND_EPISODE_ID = UUID("01986e76-4ec6-7a8f-8000-000000000002")
SECOND_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13")


def test_command_idempotency_replays_only_the_original_command_identity() -> None:
    dispatcher = InMemoryCommandDispatcher()

    receipt = dispatcher.submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=FIRST_EPISODE_ID,
        command="render",
        idempotency_key="shared-key",
    )

    assert (
        dispatcher.submit(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=FIRST_EPISODE_ID,
            command="render",
            idempotency_key="shared-key",
        )
        == receipt
    )
    for project_id, episode_id, command in (
        (SECOND_PROJECT_ID, FIRST_EPISODE_ID, "render"),
        (PROJECT_ID, SECOND_EPISODE_ID, "render"),
        (PROJECT_ID, FIRST_EPISODE_ID, "publish"),
    ):
        with pytest.raises(IdempotencyConflict):
            dispatcher.submit(
                tenant_id=TENANT_ID,
                project_id=project_id,
                episode_id=episode_id,
                command=command,  # type: ignore[arg-type]
                idempotency_key="shared-key",
            )


def test_concurrent_submission_returns_one_stable_receipt() -> None:
    """Catch concurrent accepted commands overwriting the stored receipt."""
    barrier = Barrier(2)

    def clock() -> datetime:
        barrier.wait(timeout=1)
        return datetime(2026, 8, 10, 4, 0, tzinfo=UTC)

    dispatcher = InMemoryCommandDispatcher(clock=clock)

    def submit():
        return dispatcher.submit(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=FIRST_EPISODE_ID,
            command="render",
            idempotency_key="shared-key",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = tuple(executor.map(lambda _: submit(), range(2)))

    assert first == second
