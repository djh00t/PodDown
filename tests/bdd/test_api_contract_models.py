"""BDD bindings for production API model contracts."""

from __future__ import annotations

from decimal import Decimal

from pydantic import ValidationError
from pytest_bdd import given, scenarios, then, when

from poddown.api.models import (
    EpisodeFailure,
    EpisodeStatusResponse,
    RenderCommandRequest,
)

scenarios("../features/api_contract_models.feature")


@given("a production episode status model", target_fixture="status")
def production_episode_status() -> EpisodeStatusResponse:
    """Build a safe, deterministic status projection with production references."""
    return EpisodeStatusResponse(
        episode_id="01986e76-4ec6-7b00-8000-000000000001",
        episode_version_id="01986e76-4ec6-7b00-8000-000000000005",
        version=3,
        stage="packaged",
        progress=0.8,
        workflow_id="episode-production-01986e76",
        package_manifest_sha256="a" * 64,
        publication_id="01986e76-4ec6-7b00-8000-000000000006",
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
        "episode_version_id": "01986e76-4ec6-7b00-8000-000000000005",
        "version": 3,
        "stage": "packaged",
        "progress": 0.8,
        "failure": {
            "code": "provider_failure",
            "stage": "render",
            "status": 502,
            "retriable": False,
        },
        "workflow_id": "episode-production-01986e76",
        "package_manifest_sha256": "a" * 64,
        "publication_id": "01986e76-4ec6-7b00-8000-000000000006",
    }


@given(
    "a JSON render request with a decimal-string cost ceiling",
    target_fixture="render_request",
)
def decimal_string_render_request() -> RenderCommandRequest:
    """Build a request from its JSON wire representation."""
    return RenderCommandRequest.model_validate_json('{"max_cost":"12.50"}')


@when("numeric JSON cost ceiling is submitted", target_fixture="numeric_rejected")
def numeric_json_cost_ceiling() -> bool:
    """Attempt a numeric JSON cost ceiling through the real model boundary."""
    try:
        RenderCommandRequest.model_validate_json('{"max_cost":12.50}')
    except ValidationError:
        return True
    return False


@then("the string ceiling is preserved and the numeric ceiling is rejected")
def decimal_string_cost_contract(
    render_request: RenderCommandRequest, numeric_rejected: bool
) -> None:
    """Require the frozen JSON contract to reject a numeric cost ceiling."""
    assert render_request.max_cost == Decimal("12.50")
    assert numeric_rejected is True
