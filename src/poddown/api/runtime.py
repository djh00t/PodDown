"""Deterministic command dispatch ports for the offline Episode API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID

from uuid6 import uuid7

from poddown.api.models import CommandReceipt
from poddown.episode_service import IdempotencyConflict

CommandName = Literal["create", "render", "publish"]


class CommandDispatcher(Protocol):
    """Port for handing accepted commands to a later workflow adapter."""

    def submit(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
    ) -> CommandReceipt:
        """Accept or replay one command without waiting for its workflow."""

    def replay(
        self,
        *,
        tenant_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
    ) -> CommandReceipt | None:
        """Return a previously accepted command without accepting a new one."""


class InMemoryCommandDispatcher:
    """Offline receipt store with tenant-safe command idempotency."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock
        self._lock = Lock()
        self._receipts: dict[
            tuple[UUID, str],
            tuple[tuple[UUID, UUID, CommandName], CommandReceipt],
        ] = {}

    def submit(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
    ) -> CommandReceipt:
        """Return one stable receipt per tenant, episode, command, and key."""
        key = (tenant_id, idempotency_key)
        identity = (project_id, episode_id, command)
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                if existing[0] != identity:
                    raise IdempotencyConflict()
                return existing[1]

        created_at = self._clock()
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                if existing[0] != identity:
                    raise IdempotencyConflict()
                return existing[1]
            receipt = CommandReceipt(
                command_id=uuid7(),
                episode_id=episode_id,
                idempotency_key=idempotency_key,
                command=command,
                accepted=True,
                state="queued",
                created_at=created_at,
            )
            self._receipts[key] = (identity, receipt)
            return receipt

    def replay(
        self,
        *,
        tenant_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
    ) -> CommandReceipt | None:
        """Return an existing receipt without changing dispatch state."""
        with self._lock:
            existing = self._receipts.get((tenant_id, idempotency_key))
        if existing is None or existing[0][1:] != (episode_id, command):
            return None
        return existing[1]
