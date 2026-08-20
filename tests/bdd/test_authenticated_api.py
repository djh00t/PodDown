"""BDD bindings for API-mode principal-derived scope."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from poddown.api import create_app
from poddown.auth import AuthenticatedPrincipal, AuthSettings

scenarios("../features/authenticated_api.feature")


TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")


@dataclass
class _Verifier:
    principal: AuthenticatedPrincipal

    def verify(self, authorization_header: str) -> AuthenticatedPrincipal:
        if authorization_header != "Bearer verified-token":
            raise ValueError("unexpected test token")
        return self.principal


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        issuer="https://issuer.example.test/",
        subject="api-user",
        tenant_id=TENANT,
        project_ids=frozenset({PROJECT}),
        scopes=frozenset({"episodes:write", "episodes:read", "episodes:publish"}),
        claims={"test": True},
    )


@given("an API app with a verified principal")
def api_app(context) -> None:
    settings = AuthSettings(
        mode="api",
        issuer="https://issuer.example.test/",
        audience="poddown-api",
        verification_key=secrets.token_bytes(32),
    )
    context.values["client"] = TestClient(
        create_app(
            auth_settings=settings,
            principal_verifier=_Verifier(_principal()),
        )
    )


@when("I create an episode with a bearer authorization")
def create_with_bearer(context) -> None:
    context.values["response"] = context.values["client"].post(
        "/v1/episodes",
        headers={
            "Authorization": "Bearer verified-token",
            "Idempotency-Key": "api-create-1",
        },
        json={"source": "# API scoped episode\n", "profile": "default"},
    )


@then("the episode scope comes from the principal")
def principal_scope_used(context) -> None:
    response = context.values["response"]
    assert response.status_code == 202
    body = response.json()
    assert body["episode"]["tenant_id"] == str(TENANT)
    assert body["episode"]["project_id"] == str(PROJECT)


@when("I create an episode with tenant and project headers")
def create_with_headers(context) -> None:
    context.values["response"] = context.values["client"].post(
        "/v1/episodes",
        headers={
            "Authorization": "Bearer verified-token",
            "X-Tenant-ID": str(TENANT),
            "X-Project-ID": str(PROJECT),
            "Idempotency-Key": "api-create-2",
        },
        json={"source": "# API scoped episode\n", "profile": "default"},
    )


@then("the API rejects compatibility scope headers")
def headers_rejected(context) -> None:
    response = context.values["response"]
    assert response.status_code == 400
    assert response.json()["code"] == "scope_headers_forbidden"


@when("I request publication with only the compatibility authorization header")
def publish_with_legacy_header(context) -> None:
    create = context.values["client"].post(
        "/v1/episodes",
        headers={
            "Authorization": "Bearer verified-token",
            "Idempotency-Key": "api-publish-create-1",
        },
        json={"source": "# API scoped episode\n", "profile": "default"},
    )
    episode_id = create.json()["episode"]["id"]
    context.values["response"] = context.values["client"].post(
        f"/v1/episodes/{episode_id}/publish",
        headers={
            "Authorization": "Bearer verified-token",
            "Idempotency-Key": "api-publish-1",
            "X-Publish-Authorization": "true",
        },
    )


@then("the API rejects the legacy publish authorization")
def legacy_publish_rejected(context) -> None:
    response = context.values["response"]
    assert response.status_code == 403
    assert response.json()["code"] == "publish_authorization_required"
