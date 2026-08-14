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


def test_render_command_request_preserves_optional_route_mode_and_cost_ceiling() -> (
    None
):
    """Catch render requests that drop production dispatch controls."""
    request = RenderCommandRequest(
        provider_route_id="route-live-elevenlabs-v1",
        mode="live-provider",
        max_cost="12.50",
    )

    assert request.model_dump(mode="json") == {
        "provider_route_id": "route-live-elevenlabs-v1",
        "mode": "live-provider",
        "max_cost": "12.50",
    }


def test_render_command_max_cost_has_a_decimal_string_json_contract() -> None:
    """Catch wire schemas that admit numeric cost ceilings."""
    schema = RenderCommandRequest.model_json_schema()
    wire_types = {
        option["type"] for option in schema["properties"]["max_cost"]["anyOf"]
    }
    accepted = RenderCommandRequest.model_validate_json('{"max_cost":"12.50"}')
    bodyless = RenderCommandRequest.model_validate_json("{}")

    with pytest.raises(ValidationError):
        RenderCommandRequest.model_validate_json('{"max_cost":12.50}')

    assert wire_types == {"string", "null"}
    assert accepted.max_cost == Decimal("12.50")
    assert bodyless.max_cost is None


def test_render_command_accepts_legacy_input_aliases_but_serializes_frozen_keys() -> (
    None
):
    """Keep existing callers compatible without leaking legacy wire names."""
    request = RenderCommandRequest(
        provider_route_id="route-local-v1",
        execution_mode="deterministic-local",
        cost_ceiling="1.00",
    )

    assert request.mode == "deterministic-local"
    assert request.max_cost == Decimal("1.00")
    assert request.model_dump(mode="json") == {
        "provider_route_id": "route-local-v1",
        "mode": "deterministic-local",
        "max_cost": "1.00",
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


@pytest.mark.parametrize("target_id", ["", "   ", "\n"])
def test_publish_command_request_rejects_blank_target_id(target_id: str) -> None:
    """Catch publish requests that accept unusable target identifiers."""
    with pytest.raises(ValidationError):
        PublishCommandRequest(
            target_id=target_id,
            approval_id="01986e76-4ec6-7b00-8000-000000000004",
        )


def test_publish_command_request_rejects_non_uuidv7_approval() -> None:
    """Catch publish approvals that do not use the required UUIDv7 identity."""
    with pytest.raises(ValidationError, match="UUIDv7"):
        PublishCommandRequest(
            target_id="transistor-show-001",
            approval_id="550e8400-e29b-41d4-a716-446655440000",
        )


def test_status_exposes_production_links_and_only_safe_failure_fields() -> None:
    """Catch status contracts that leak failure details or omit production links."""
    status = EpisodeStatusResponse(
        episode_id=EPISODE_ID,
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

    assert status.model_dump(mode="json") == {
        "episode_id": EPISODE_ID,
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


@pytest.mark.parametrize("stage", ["validated", "package", "unknown"])
def test_status_rejects_non_production_stage_names(stage: str) -> None:
    """Prevent internal lifecycle names from crossing the public API boundary."""
    with pytest.raises(ValidationError):
        EpisodeStatusResponse(
            episode_id=EPISODE_ID,
            episode_version_id="01986e76-4ec6-7b00-8000-000000000005",
            version=1,
            stage=stage,
            progress=0.0,
        )


def test_status_rejects_non_uuidv7_episode_version_id() -> None:
    """Keep the durable episode-version identity aligned with UUIDv7."""
    with pytest.raises(ValidationError):
        EpisodeStatusResponse(
            episode_id=EPISODE_ID,
            episode_version_id="550e8400-e29b-41d4-a716-446655440000",
            version=1,
            stage="ingested",
            progress=0.0,
        )


def test_status_rejects_non_uuidv7_publication_id() -> None:
    """Keep publication references aligned with UUIDv7 approvals and receipts."""
    with pytest.raises(ValidationError):
        EpisodeStatusResponse(
            episode_id=EPISODE_ID,
            episode_version_id="01986e76-4ec6-7b00-8000-000000000005",
            version=1,
            stage="ingested",
            progress=0.0,
            publication_id="550e8400-e29b-41d4-a716-446655440000",
        )


@pytest.mark.parametrize("progress", [-0.01, 1.01])
def test_status_rejects_progress_outside_zero_to_one(progress: float) -> None:
    """Prevent clients from receiving impossible workflow progress values."""
    with pytest.raises(ValidationError):
        EpisodeStatusResponse(
            episode_id=EPISODE_ID,
            episode_version_id="01986e76-4ec6-7b00-8000-000000000005",
            version=1,
            stage="ingested",
            progress=progress,
        )
