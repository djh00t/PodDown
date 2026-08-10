"""Unit contracts for command receipt idempotency scope."""

from __future__ import annotations

from uuid import UUID

from poddown.api.runtime import InMemoryCommandDispatcher

TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
FIRST_EPISODE_ID = UUID("01986e76-4ec6-7a8f-8000-000000000001")
SECOND_EPISODE_ID = UUID("01986e76-4ec6-7a8f-8000-000000000002")


def test_command_idempotency_is_scoped_by_tenant_episode_command_and_key() -> None:
    dispatcher = InMemoryCommandDispatcher()

    create = dispatcher.submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=FIRST_EPISODE_ID,
        command="create",
        idempotency_key="shared-key",
    )
    render = dispatcher.submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=FIRST_EPISODE_ID,
        command="render",
        idempotency_key="shared-key",
    )
    other_episode_render = dispatcher.submit(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        episode_id=SECOND_EPISODE_ID,
        command="render",
        idempotency_key="shared-key",
    )

    assert create.command_id != render.command_id
    assert render.command_id != other_episode_render.command_id
    assert (
        dispatcher.submit(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            episode_id=FIRST_EPISODE_ID,
            command="render",
            idempotency_key="shared-key",
        )
        == render
    )
