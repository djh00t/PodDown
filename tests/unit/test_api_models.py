"""Pure contract tests for the planned M3 HTTP API models."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from poddown.api.models import (
    CommandReceipt,
    EpisodeCreateRequest,
    EpisodeFailure,
    EpisodeStatusResponse,
    EpisodeSummary,
    ProblemDetail,
    PublishCommandRequest,
    RenderCommandRequest,
    RequestContext,
)

TENANT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
EPISODE_ID = "01986e76-4ec6-7a8f-8000-000000000001"


def test_create_request_accepts_markdown_and_registered_profile() -> None:
    request = EpisodeCreateRequest(
        source="# Offline API fixture\n",
        profile="technical-dialogue",
    )

    assert request.source == "# Offline API fixture\n"
    assert request.profile == "technical-dialogue"


@pytest.mark.parametrize(
    "payload",
    [
        {"source": "", "profile": "technical-dialogue"},
        {"source": "# title", "profile": ""},
        {"source": "# title"},
        {"profile": "technical-dialogue"},
    ],
)
def test_create_request_rejects_missing_or_empty_source_and_profile(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        EpisodeCreateRequest(**payload)


def test_request_context_parses_required_demo_headers() -> None:
    context = RequestContext.from_headers(
        {
            "X-Tenant-ID": TENANT_ID,
            "X-Project-ID": PROJECT_ID,
            "Idempotency-Key": "create-fixture-001",
        }
    )

    assert context.tenant_id == UUID(TENANT_ID)
    assert context.project_id == UUID(PROJECT_ID)
    assert context.idempotency_key == "create-fixture-001"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {
            "X-Tenant-ID": "not-a-uuid",
            "X-Project-ID": PROJECT_ID,
            "Idempotency-Key": "key",
        },
        {
            "X-Tenant-ID": TENANT_ID,
            "X-Project-ID": "not-a-uuid",
            "Idempotency-Key": "key",
        },
        {"X-Tenant-ID": TENANT_ID, "X-Project-ID": PROJECT_ID, "Idempotency-Key": ""},
    ],
)
def test_request_context_rejects_missing_or_invalid_required_headers(
    headers: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        RequestContext.from_headers(headers)


def test_problem_detail_sanitizes_source_and_secret_like_values() -> None:
    source = "# Customer-only source\nAPI_KEY=do-not-expose"

    problem = ProblemDetail.from_exception(
        status=422,
        code="invalid_source",
        detail=f"Could not validate {source}",
    )

    serialized = problem.model_dump(mode="json")
    assert serialized == {
        "type": "https://poddown.dev/problems/invalid_source",
        "title": "Invalid source",
        "status": 422,
        "code": "invalid_source",
        "detail": "The request source is invalid.",
    }


def test_summary_and_command_receipt_preserve_command_identity() -> None:
    summary = EpisodeSummary(
        id=EPISODE_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        version=1,
        state="created",
    )
    receipt = CommandReceipt(
        command_id="01986e76-4ec6-7b00-8000-000000000002",
        episode_id=EPISODE_ID,
        idempotency_key="create-fixture-001",
        command="create",
        accepted=True,
    )

    assert receipt.episode_id == summary.id
    assert receipt.idempotency_key == "create-fixture-001"
    assert receipt.accepted is True


@pytest.mark.parametrize(
    "state", ["queued", "dispatched", "running", "completed", "failed"]
)
def test_command_receipt_accepts_each_production_workflow_state(state: str) -> None:
    """Catch receipt contracts that reject a valid workflow state."""
    receipt = CommandReceipt(
        command_id="01986e76-4ec6-7b00-8000-000000000002",
        episode_id=EPISODE_ID,
        idempotency_key="render-fixture-001",
        command="render",
        state=state,
    )

    assert receipt.state == state


def test_render_command_request_preserves_optional_route_mode_and_cost_ceiling(
) -> None:
    """Catch render requests that drop production dispatch controls."""
    request = RenderCommandRequest(
        provider_route_id="01986e76-4ec6-7b00-8000-000000000003",
        execution_mode="live-provider",
        cost_ceiling=Decimal("12.50"),
    )

    assert request.model_dump(mode="json") == {
        "provider_route_id": "01986e76-4ec6-7b00-8000-000000000003",
        "execution_mode": "live-provider",
        "cost_ceiling": "12.50",
    }


def test_publish_command_request_requires_target_and_uuidv7_approval() -> None:
    """Catch publish requests accepted without their scoped approval identity."""
    request = PublishCommandRequest(
        target_id="transistor-show-001",
        approval_id="01986e76-4ec6-7b00-8000-000000000004",
    )

    assert request.model_dump(mode="json") == {
        "target_id": "transistor-show-001",
        "approval_id": "01986e76-4ec6-7b00-8000-000000000004",
    }


def test_status_exposes_production_links_and_only_safe_failure_fields() -> None:
    """Catch status contracts that leak failure details or omit production links."""
    status = EpisodeStatusResponse(
        episode_id=EPISODE_ID,
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

    assert status.model_dump(mode="json") == {
        "episode_id": EPISODE_ID,
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
