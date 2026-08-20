"""Authoritative, tenant-project-scoped status reads for the Episode API."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Literal, Protocol, cast
from uuid import UUID

from poddown.api.models import EpisodeFailure, EpisodeStatusResponse, ProductionStage
from poddown.api.runtime import CommandDispatcher
from poddown.api.temporal_dispatcher import TemporalWorkflowStatus
from poddown.episode_service import (
    EpisodeApplicationService,
    EpisodeNotFound,
    EpisodeRecord,
    EpisodeState,
    StructuredFailure,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LiteralStage = Literal["packaged", "published"]


class EpisodeStatusRepository(Protocol):
    """Read the authoritative status contract inside one tenant/project scope."""

    def get_status(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
    ) -> EpisodeStatusResponse | None:
        """Return the scoped status, or no record without crossing scope."""


class TemporalWorkflowStatusReader(Protocol):
    """Read one workflow without exposing provider or exception details."""

    def get_workflow_status(self, workflow_id: str) -> TemporalWorkflowStatus:
        """Return the normalized Temporal execution status."""


class EpisodeServiceStatusRepository:
    """Deterministic status adapter for an injected episode-service repository."""

    def __init__(self, service: EpisodeApplicationService) -> None:
        self._service = service

    def get_status(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
    ) -> EpisodeStatusResponse | None:
        """Project persisted episode state without mutating it."""
        record = self._service.get_episode(tenant_id, episode_id)
        if record.project_id != project_id:
            return None
        return _status_from_record(record)


class TemporalEpisodeStatusRepository:
    """Project completed Temporal evidence over the durable episode record."""

    def __init__(
        self,
        service: EpisodeApplicationService,
        *,
        dispatcher: CommandDispatcher,
        workflow_reader: TemporalWorkflowStatusReader,
    ) -> None:
        self._service = service
        self._base = EpisodeServiceStatusRepository(service)
        self._dispatcher = dispatcher
        self._workflow_reader = workflow_reader

    def get_status(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
    ) -> EpisodeStatusResponse | None:
        """Return durable status enriched only by verified workflow evidence."""
        try:
            status = self._base.get_status(
                tenant_id=tenant_id,
                project_id=project_id,
                episode_id=episode_id,
            )
        except EpisodeNotFound:
            return None
        if status is None:
            return None
        record = self._service.get_episode(tenant_id, episode_id)
        if record.project_id != project_id:
            return None
        try:
            receipt = self._dispatcher.replay(
                tenant_id=tenant_id,
                project_id=project_id,
                episode_id=episode_id,
                command="create",
                idempotency_key=record.idempotency_key,
            )
        except Exception:
            return status
        if receipt is None or receipt.workflow_id is None:
            return status
        workflow_id = receipt.workflow_id
        try:
            workflow_status = self._workflow_reader.get_workflow_status(workflow_id)
        except Exception:
            return status.model_copy(update={"workflow_id": workflow_id})
        return _project_temporal_status(status, workflow_id, workflow_status)


def _project_temporal_status(
    status: EpisodeStatusResponse,
    workflow_id: str,
    workflow_status: TemporalWorkflowStatus,
) -> EpisodeStatusResponse:
    """Apply only allowlisted terminal workflow fields to API status."""
    common = {"workflow_id": workflow_id}
    if workflow_status.state == "running":
        return status.model_copy(
            update={**common, "stage": "rendering", "progress": 0.5}
        )
    if workflow_status.state == "completed":
        result = _production_result_fields(workflow_status.result)
        if result is None:
            return status.model_copy(update=common)
        stage, manifest_sha256, publication_id = result
        return status.model_copy(
            update={
                **common,
                "stage": stage,
                "progress": 1.0 if stage == "published" else 0.9,
                "package_manifest_sha256": manifest_sha256,
                "publication_id": publication_id,
            }
        )
    if workflow_status.state in {
        "failed",
        "canceled",
        "terminated",
        "continued_as_new",
        "timed_out",
    }:
        return status.model_copy(
            update={
                **common,
                "stage": "failed",
                "progress": 1.0,
                "failure": EpisodeFailure(
                    code="workflow_failed",
                    stage="workflow",
                    status=502,
                    retriable=False,
                ),
            }
        )
    return status.model_copy(update=common)


def _production_result_fields(
    result: str | None,
) -> tuple[LiteralStage, str, UUID | None] | None:
    """Decode only the completed production result fields needed by status."""
    if result is None:
        return None
    try:
        value = json.loads(result)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, Mapping) or value.get("status") != "completed":
        return None
    stage = value.get("stage")
    package_sha256 = value.get("package_sha256")
    manifest_sha256 = value.get("package_manifest_sha256")
    if (
        stage not in {"packaged", "published"}
        or not isinstance(package_sha256, str)
        or _SHA256.fullmatch(package_sha256) is None
        or not isinstance(manifest_sha256, str)
        or _SHA256.fullmatch(manifest_sha256) is None
    ):
        return None
    publication_id = _publication_id(value.get("publication"))
    return cast(
        "tuple[LiteralStage, str, UUID | None]",
        (stage, manifest_sha256, publication_id),
    )


def _publication_id(value: object) -> UUID | None:
    """Expose a publication UUID only when the worker result carries UUIDv7."""
    if not isinstance(value, Mapping):
        return None
    candidate = value.get("publication_id")
    if not isinstance(candidate, str):
        return None
    try:
        publication_id = UUID(candidate)
    except ValueError:
        return None
    return publication_id if publication_id.version == 7 else None


def _status_from_record(record: EpisodeRecord) -> EpisodeStatusResponse:
    """Map persisted lifecycle fields to the frozen safe status contract."""
    return EpisodeStatusResponse(
        episode_id=record.episode_id,
        episode_version_id=record.episode_id,
        version=record.version,
        stage=_status_stage(record.state),
        progress=_progress(record.state),
        failure=_safe_failure(record.failure) if record.failure is not None else None,
        package_manifest_sha256=record.package_manifest_sha256,
    )


def _progress(state: EpisodeState) -> float:
    """Expose deterministic coarse progress for persisted lifecycle state."""
    return {
        EpisodeState.VALIDATED: 0.0,
        EpisodeState.SCRIPTED: 0.2,
        EpisodeState.RENDERED: 0.5,
        EpisodeState.QA_PASSED: 0.7,
        EpisodeState.PACKAGED: 0.9,
        EpisodeState.PUBLISHED: 1.0,
        EpisodeState.FAILED: 1.0,
    }[state]


def _status_stage(state: EpisodeState) -> ProductionStage:
    """Map persisted service states to frozen production status stages."""
    return cast(
        ProductionStage,
        {
            EpisodeState.VALIDATED: "ingested",
            EpisodeState.SCRIPTED: "prepared",
            EpisodeState.RENDERED: "rendering",
            EpisodeState.QA_PASSED: "qa",
            EpisodeState.PACKAGED: "packaged",
            EpisodeState.PUBLISHED: "published",
            EpisodeState.FAILED: "failed",
        }[state],
    )


def _safe_failure(failure: StructuredFailure) -> EpisodeFailure:
    """Allowlist only polling-safe structured failure fields."""
    return EpisodeFailure(
        code=failure.code,
        stage=failure.stage,
        status=failure.status,
        retriable=failure.retriable,
    )
