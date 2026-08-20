"""Tests for the bounded local Temporal test-server startup helper."""

from __future__ import annotations

import asyncio

from tests.temporal_support import retry_local_temporal_environment


class _FakeEnvironment:
    async def __aenter__(self) -> _FakeEnvironment:
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback


def test_retries_only_transient_temporal_startup_failures() -> None:
    """A transient SDK dev-server startup error gets bounded retries."""
    environment = _FakeEnvironment()
    attempts = 0

    async def starter() -> _FakeEnvironment:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(
                "Failed starting Temporal dev server: connection refused"
            )
        return environment

    async def run() -> None:
        async with retry_local_temporal_environment(
            starter, retry_delay_seconds=0
        ) as actual:
            assert actual is environment

    asyncio.run(run())
    assert attempts == 3
