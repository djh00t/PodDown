"""Deterministic command dispatch ports for the offline Episode API."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID

from uuid6 import uuid7

from poddown.api.models import CommandReceipt
from poddown.episode_service import IdempotencyConflict

CommandName = Literal["create", "render", "publish"]


def _payload_text(payload: Mapping[str, object] | None) -> str:
    """Canonicalize a JSON-shaped command payload for replay comparison."""
    if payload is not None and not isinstance(payload, Mapping):
        raise ValueError("command payload must be a mapping")
    try:
        return json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("command payload must be JSON-shaped") from error


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
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Accept or replay one command without waiting for its workflow."""

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID | None = None,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
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
        self._payloads: dict[tuple[UUID, str], str] = {}

    def submit(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt:
        """Return one stable receipt per tenant, episode, command, and key."""
        payload_text = _payload_text(payload)
        key = (tenant_id, idempotency_key)
        identity = (project_id, episode_id, command)
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                if existing[0] != identity:
                    raise IdempotencyConflict()
                if self._payloads.get(key, "{}") != payload_text:
                    raise IdempotencyConflict()
                return existing[1]

        created_at = self._clock()
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                if existing[0] != identity:
                    raise IdempotencyConflict()
                if self._payloads.get(key, "{}") != payload_text:
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
            self._payloads[key] = payload_text
            return receipt

    def replay(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID | None = None,
        episode_id: UUID,
        command: CommandName,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
    ) -> CommandReceipt | None:
        """Return an existing receipt without changing dispatch state."""
        payload_text = _payload_text(payload)
        with self._lock:
            existing = self._receipts.get((tenant_id, idempotency_key))
        if existing is None or (
            existing[0][1:] != (episode_id, command)
            or (project_id is not None and existing[0][0] != project_id)
        ):
            return None
        if (
            payload is not None
            and self._payloads.get((tenant_id, idempotency_key), "{}") != payload_text
        ):
            raise IdempotencyConflict()
        return existing[1]
