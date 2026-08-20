"""Repository-backed Temporal activity for authoritative episode status reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from poddown.api.models import EpisodeStatusResponse
from poddown.api.status import EpisodeStatusRepository
from poddown.episode_service import EpisodeNotFound

type StatusActivityHandler = Callable[["StatusActivityRequest"], EpisodeStatusResponse]


@dataclass(frozen=True, slots=True)
class StatusActivityRequest:
    """Tenant and project scope for one authoritative status read."""

    tenant_id: UUID
    project_id: UUID
    episode_id: UUID


def _not_found_error() -> ApplicationError:
    return ApplicationError(
        "episode status lookup failed",
        {
            "code": "episode_not_found",
            "stage": "lookup",
            "status": 404,
            "retriable": False,
        },
        type="EpisodeStatusLookupError",
        non_retryable=True,
    )


def build_status_activity(repository: EpisodeStatusRepository) -> StatusActivityHandler:
    """Build a Temporal activity from an injected authoritative repository."""

    @activity.defn(name="poddown.episode_status")
    def get_status(request: StatusActivityRequest) -> EpisodeStatusResponse:
        try:
            status = repository.get_status(
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                episode_id=request.episode_id,
            )
        except EpisodeNotFound:
            raise _not_found_error() from None
        if status is None or status.episode_id != request.episode_id:
            raise _not_found_error()
        return status

    return get_status


__all__ = [
    "EpisodeStatusRepository",
    "StatusActivityHandler",
    "StatusActivityRequest",
    "build_status_activity",
]
