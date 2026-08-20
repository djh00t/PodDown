from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from poddown.agent_mcp import InMemoryApprovalRegistry


def test_concurrent_valid_approval_is_consumed_exactly_once():
    now = datetime(2026, 8, 10, tzinfo=UTC)
    registry = InMemoryApprovalRegistry(clock=lambda: now)
    registry.issue(
        "tenant-a",
        "episode-1",
        "approval-1",
        target_id="target-1",
        expires_at=now + timedelta(minutes=5),
    )
    start = Barrier(2)

    def consume() -> bool:
        start.wait()
        return registry.verify_and_consume(
            "tenant-a", "episode-1", "approval-1", "target-1"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))
    assert results.count(True) == 1
    assert results.count(False) == 1
