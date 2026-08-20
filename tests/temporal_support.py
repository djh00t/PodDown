"""Shared safeguards for tests that start a local Temporal dev server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

_STARTUP_ERROR = "Failed starting Temporal dev server"


@asynccontextmanager
async def retry_local_temporal_environment[EnvironmentT](
    starter: Callable[[], Awaitable[AbstractAsyncContextManager[EnvironmentT]]],
    *,
    max_attempts: int = 3,
    retry_delay_seconds: float = 0.25,
) -> AsyncIterator[EnvironmentT]:
    """Retry only the SDK's transient local dev-server startup failure."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must not be negative")

    for attempt in range(max_attempts):
        try:
            environment = await starter()
        except RuntimeError as error:
            is_startup_error = _STARTUP_ERROR in str(error)
            if not is_startup_error or attempt == max_attempts - 1:
                raise
            await asyncio.sleep(retry_delay_seconds * (attempt + 1))
        else:
            async with environment as active_environment:
                yield active_environment
            return

    raise AssertionError("local Temporal startup retry loop did not return")
