"""BDD bindings for repository-backed workflow status activity reads."""

from dataclasses import dataclass
from uuid import UUID

from pytest_bdd import given, scenarios, then, when
from temporalio.exceptions import ApplicationError

from poddown.api.models import EpisodeFailure, EpisodeStatusResponse
from tests.bdd.conftest import ScenarioContext

TENANT_ID = UUID("01986e76-4ec6-7b00-8000-000000000001")
OTHER_TENANT_ID = UUID("01986e76-4ec6-7b00-8000-000000000002")
PROJECT_ID = UUID("01986e76-4ec6-7b00-8000-000000000003")
OTHER_PROJECT_ID = UUID("01986e76-4ec6-7b00-8000-000000000008")
EPISODE_ID = UUID("01986e76-4ec6-7b00-8000-000000000004")
VERSION_ID = UUID("01986e76-4ec6-7b00-8000-000000000005")
PUBLICATION_ID = UUID("01986e76-4ec6-7b00-8000-000000000006")

scenarios("../features/status_activity.feature")


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


def _status(*, failure: EpisodeFailure | None = None) -> EpisodeStatusResponse:
    return EpisodeStatusResponse(
        episode_id=EPISODE_ID,
        episode_version_id=VERSION_ID,
        version=3,
        stage="failed" if failure is not None else "packaged",
        progress=1.0 if failure is not None else 0.9,
        workflow_id="episode-production-01986e76",
        package_manifest_sha256="a" * 64,
        publication_id=PUBLICATION_ID,
        failure=failure,
    )


def _read(
    context: ScenarioContext,
    tenant_id: UUID = TENANT_ID,
    project_id: UUID = PROJECT_ID,
) -> None:
    from poddown.audio.status_activity import (
        StatusActivityRequest,
        build_status_activity,
    )

    activity = build_status_activity(context.values["repository"])
    try:
        context.values["result"] = activity(
            StatusActivityRequest(tenant_id, project_id, EPISODE_ID)
        )
    except ApplicationError as error:
        context.values["error"] = error


@given("an authoritative status record for a tenant project and episode")
def authoritative_status_record(context: ScenarioContext) -> None:
    status = _status()
    context.values["status"] = status
    context.values["repository"] = _Repository(
        {(TENANT_ID, PROJECT_ID, EPISODE_ID): status}
    )


@given("no authoritative status record")
def no_authoritative_status_record(context: ScenarioContext) -> None:
    context.values["repository"] = _Repository({})


@given("an authoritative failed status record with internal failure detail")
def failed_authoritative_status_record(context: ScenarioContext) -> None:
    # The activity receives only the API's allowlisted EpisodeFailure contract.
    status = _status(
        failure=EpisodeFailure(
            code="provider_failure",
            stage="render",
            status=502,
            retriable=False,
        )
    )
    context.values["status"] = status
    context.values["repository"] = _Repository(
        {(TENANT_ID, PROJECT_ID, EPISODE_ID): status}
    )


@when("the status activity reads that tenant project and episode")
def read_scoped_status(context: ScenarioContext) -> None:
    _read(context)


@when("the status activity reads a tenant project and episode")
def read_missing_status(context: ScenarioContext) -> None:
    _read(context)


@when("the status activity reads it as another tenant")
def read_cross_tenant_status(context: ScenarioContext) -> None:
    _read(context, OTHER_TENANT_ID)


@when("the status activity reads it from another project")
def read_cross_project_status(context: ScenarioContext) -> None:
    _read(context, project_id=OTHER_PROJECT_ID)


@then("it returns the complete authoritative status contract")
def returns_authoritative_status(context: ScenarioContext) -> None:
    assert context.values["result"] == context.values["status"]


@then("the status activity reports a safe not-found lookup error")
def safe_not_found_error(context: ScenarioContext) -> None:
    error = context.values["error"]
    assert error.type == "EpisodeStatusLookupError"
    assert error.non_retryable is True
    assert error.details == (
        {
            "code": "episode_not_found",
            "stage": "lookup",
            "status": 404,
            "retriable": False,
        },
    )
    assert "tenant" not in str(error).lower()
    assert "project" not in str(error).lower()


@then("its failure projection contains only safe status fields")
def safe_failure_projection(context: ScenarioContext) -> None:
    result = context.values["result"]
    assert result.failure is not None
    assert result.failure.model_dump(mode="json") == {
        "code": "provider_failure",
        "stage": "render",
        "status": 502,
        "retriable": False,
    }
