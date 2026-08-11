"""BDD bindings for production API model contracts."""

from __future__ import annotations

from pytest_bdd import given, scenarios, then

from poddown.api.models import EpisodeFailure, EpisodeStatusResponse

scenarios("../features/api_contract_models.feature")


@given("a production episode status model", target_fixture="status")
def production_episode_status() -> EpisodeStatusResponse:
    """Build a safe, deterministic status projection with production references."""
    return EpisodeStatusResponse(
        episode_id="01986e76-4ec6-7b00-8000-000000000001",
        version=3,
        stage="package",
        progress=0.8,
        workflow_id="episode-production-01986e76",
        package_manifest_checksum="a" * 64,
        publication_id="publication-001",
        failure=EpisodeFailure(
            code="provider_failure",
            stage="render",
            status=502,
            retriable=False,
        ),
    )


@then("it exposes only allowlisted failure details and production references")
def safe_status_projection(status: EpisodeStatusResponse) -> None:
    """Require status to preserve identifiers without raw error content."""
    assert status.model_dump(mode="json") == {
        "episode_id": "01986e76-4ec6-7b00-8000-000000000001",
        "version": 3,
        "stage": "package",
        "progress": 0.8,
        "failure": {
            "code": "provider_failure",
            "stage": "render",
            "status": 502,
            "retriable": False,
        },
        "workflow_id": "episode-production-01986e76",
        "package_manifest_checksum": "a" * 64,
        "publication_id": "publication-001",
    }
