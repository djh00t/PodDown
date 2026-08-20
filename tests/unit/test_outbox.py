"""Unit contracts for the transactional outbox boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from poddown.outbox import OutboxEvent


def test_outbox_event_requires_uuidv7_and_utc_timestamp() -> None:
    with pytest.raises(ValueError, match="UUIDv7"):
        OutboxEvent(
            tenant_id=UUID("00000000-0000-4000-8000-000000000000"),
            project_id=UUID("018f2c8b-7b46-7cc5-b2e1-222222222222"),
            event_id=UUID("018f2c8b-7b46-7cc5-b2e1-333333333333"),
            aggregate_type="episode",
            aggregate_id=UUID("018f2c8b-7b46-7cc5-b2e1-444444444444"),
            event_type="episode.created",
            payload={"ok": True},
            created_at=datetime(2026, 8, 14, tzinfo=UTC),
        )
