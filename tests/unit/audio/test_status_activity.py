"""Tests for the repository-backed Temporal episode status activity."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from temporalio.exceptions import ApplicationError

from poddown.api.models import EpisodeFailure, EpisodeStatusResponse
from poddown.audio.status_activity import EpisodeStatusRepository, StatusActivityHandler
from poddown.episode_service import EpisodeNotFound

TENANT_ID = UUID("01986e76-4ec6-7b00-8000-000000000001")
OTHER_TENANT_ID = UUID("01986e76-4ec6-7b00-8000-000000000002")
PROJECT_ID = UUID("01986e76-4ec6-7b00-8000-000000000003")
OTHER_PROJECT_ID = UUID("01986e76-4ec6-7b00-8000-000000000008")
EPISODE_ID = UUID("01986e76-4ec6-7b00-8000-000000000004")
VERSION_ID = UUID("01986e76-4ec6-7b00-8000-000000000005")
PUBLICATION_ID = UUID("01986e76-4ec6-7b00-8000-000000000006")


def _status() -> EpisodeStatusResponse:
    return EpisodeStatusResponse(
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        version=3,
        stage="failed",
        progress=1.0,
        workflow_id="episode-production-01986e76",
        package_manifest_sha256="a" * 64,
        publication_id=PUBLICATION_ID,
        failure=EpisodeFailure(
            code="provider_failure",
            stage="render",
            status=502,
            retriable=False,
        ),
    )


@dataclass
class _Repository:
    records: dict[tuple[UUID, UUID, UUID], EpisodeStatusResponse]

    def get_status(
        self,
        *,
        tenant_id: UUID,
        project_id: UUID,
        episode_id: UUID,
    ) -> EpisodeStatusResponse | None:
        return self.records.get((tenant_id, project_id, episode_id))


def _activity(repository: EpisodeStatusRepository) -> StatusActivityHandler:
    from poddown.audio.status_activity import build_status_activity

    return build_status_activity(repository)


def test_status_activity_returns_the_authoritative_scoped_status_contract() -> None:
    """Changing a scoped record or dropping a response field must be observable."""
    from poddown.audio.status_activity import StatusActivityRequest

    expected = _status()

    result = _activity(_Repository({(TENANT_ID, PROJECT_ID, EPISODE_ID): expected}))(
        StatusActivityRequest(TENANT_ID, PROJECT_ID, EPISODE_ID)
    )

    assert result == expected
    assert result.model_dump(mode="json") == {
        "episode_id": str(EPISODE_ID),
        "episode_version_id": str(VERSION_ID),
        "version": 3,
        "stage": "failed",
        "progress": 1.0,
        "failure": {
            "code": "provider_failure",
            "stage": "render",
            "status": 502,
            "retriable": False,
        },
        "workflow_id": "episode-production-01986e76",
        "package_manifest_sha256": "a" * 64,
        "publication_id": str(PUBLICATION_ID),
    }


def test_status_activity_hides_another_tenants_record() -> None:
    """Removing tenant scope from the repository lookup would disclose this status."""
    from poddown.audio.status_activity import StatusActivityRequest

    activity = _activity(_Repository({(TENANT_ID, PROJECT_ID, EPISODE_ID): _status()}))

    with pytest.raises(ApplicationError) as raised:
        activity(StatusActivityRequest(OTHER_TENANT_ID, PROJECT_ID, EPISODE_ID))

    assert raised.value.type == "EpisodeStatusLookupError"
    assert raised.value.non_retryable is True
    assert raised.value.details == (
        {
            "code": "episode_not_found",
            "stage": "lookup",
            "status": 404,
            "retriable": False,
        },
    )


def test_status_activity_fails_closed_when_the_record_is_missing() -> None:
    """Returning a synthetic empty status would hide an absent authoritative record."""
    from poddown.audio.status_activity import StatusActivityRequest

    with pytest.raises(ApplicationError) as raised:
        _activity(_Repository({}))(
            StatusActivityRequest(TENANT_ID, PROJECT_ID, EPISODE_ID)
        )

    assert raised.value.type == "EpisodeStatusLookupError"
    assert raised.value.non_retryable is True
    assert "episode status lookup failed" in str(raised.value)
    assert "tenant" not in str(raised.value).lower()
    assert "project" not in str(raised.value).lower()


def test_status_activity_rejects_a_repository_status_for_another_episode() -> None:
    """Returning a status for a different episode must fail rather than leak it."""
    from poddown.audio.status_activity import StatusActivityRequest

    wrong_episode = _status().model_copy(
        update={"episode_id": UUID("01986e76-4ec6-7b00-8000-000000000007")}
    )

    with pytest.raises(ApplicationError) as raised:
        _activity(_Repository({(TENANT_ID, PROJECT_ID, EPISODE_ID): wrong_episode}))(
            StatusActivityRequest(TENANT_ID, PROJECT_ID, EPISODE_ID)
        )

    assert raised.value.type == "EpisodeStatusLookupError"
    assert raised.value.details == (
        {
            "code": "episode_not_found",
            "stage": "lookup",
            "status": 404,
            "retriable": False,
        },
    )


def test_status_activity_hides_another_projects_record() -> None:
    """Removing project scope from the repository lookup would disclose this status."""
    from poddown.audio.status_activity import StatusActivityRequest

    activity = _activity(_Repository({(TENANT_ID, PROJECT_ID, EPISODE_ID): _status()}))

    with pytest.raises(ApplicationError) as raised:
        activity(StatusActivityRequest(TENANT_ID, OTHER_PROJECT_ID, EPISODE_ID))

    assert raised.value.type == "EpisodeStatusLookupError"
    assert raised.value.non_retryable is True


def test_status_activity_normalizes_an_absent_domain_episode() -> None:
    """Allowing EpisodeNotFound through would expose a domain error to Temporal."""
    from poddown.audio.status_activity import StatusActivityRequest

    class RepositoryThatCannotFindEpisode:
        def get_status(
            self,
            *,
            tenant_id: UUID,
            project_id: UUID,
            episode_id: UUID,
        ) -> EpisodeStatusResponse | None:
            raise EpisodeNotFound()

    with pytest.raises(ApplicationError) as raised:
        _activity(RepositoryThatCannotFindEpisode())(
            StatusActivityRequest(TENANT_ID, PROJECT_ID, EPISODE_ID)
        )

    assert raised.value.type == "EpisodeStatusLookupError"
    assert raised.value.non_retryable is True
    assert "episode was not found" not in str(raised.value)


def test_status_activity_propagates_transient_storage_failures() -> None:
    """Catching storage faults would turn retryable failures into 404s."""
    from poddown.audio.status_activity import StatusActivityRequest

    class TransientStorageError(RuntimeError):
        pass

    class RepositoryWithTransientFailure:
        def get_status(
            self,
            *,
            tenant_id: UUID,
            project_id: UUID,
            episode_id: UUID,
        ) -> EpisodeStatusResponse | None:
            raise TransientStorageError("database temporarily unavailable")

    with pytest.raises(TransientStorageError, match="database temporarily unavailable"):
        _activity(RepositoryWithTransientFailure())(
            StatusActivityRequest(TENANT_ID, PROJECT_ID, EPISODE_ID)
        )
