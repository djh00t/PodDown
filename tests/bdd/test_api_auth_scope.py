"""BDD coverage for verified-principal episode API scope."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from poddown.api import create_app
from poddown.auth import AuthenticatedPrincipal
from tests.integration.test_episode_api import (
    OTHER_PROJECT_ID,
    OTHER_TENANT_ID,
    PROFILE,
    PROJECT_ID,
    SOURCE,
    TENANT_ID,
    _headers,
)

scenarios("../features/api_auth_scope.feature")


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="m03-user",
        tenant_id=UUID(TENANT_ID),
        project_ids=frozenset({UUID(PROJECT_ID)}),
        scopes=frozenset({"episodes:write"}),
        issuer="https://issuer.example.test",
    )


@given("a production episode API client with an injected verified principal")
def production_client(context) -> None:
    context.values["client"] = TestClient(
        create_app(auth_mode="oidc", principal_verifier=lambda _request: _principal())
    )


@given("a production episode API client without an injected principal")
def unauthenticated_production_client(context) -> None:
    context.values["client"] = TestClient(create_app(auth_mode="oidc"))


@given("a local episode API client without an injected principal")
def local_client(context) -> None:
    context.values["client"] = TestClient(create_app(auth_mode="local"))


@when("I submit an episode with forged tenant and project headers")
def submit_forged_scope(context) -> None:
    context.values["response"] = context.values["client"].post(
        "/v1/episodes",
        headers=_headers(tenant_id=OTHER_TENANT_ID, project_id=OTHER_PROJECT_ID),
        json={"source": SOURCE, "profile": PROFILE},
    )


@when("I submit an episode with tenant and project headers")
def submit_headers(context) -> None:
    context.values["response"] = context.values["client"].post(
        "/v1/episodes",
        headers=_headers(),
        json={"source": SOURCE, "profile": PROFILE},
    )


@when("I submit an episode for an allowlisted project")
def submit_allowlisted_project(context) -> None:
    submit_headers(context)


@then("the production scope response is forbidden")
def forbidden_scope(context) -> None:
    response = context.values["response"]
    assert response.status_code == 403
    assert response.json()["code"] in {
        "tenant_scope_mismatch",
        "project_scope_forbidden",
    }


@then("the production scope response requires an authenticated principal")
def missing_principal(context) -> None:
    response = context.values["response"]
    assert response.status_code == 401
    assert response.json()["code"] == "authenticated_principal_required"


@then("the production scope response is accepted for the verified tenant")
def accepted_scope(context) -> None:
    response = context.values["response"]
    assert response.status_code == 202
    assert response.json()["episode"]["tenant_id"] == TENANT_ID
