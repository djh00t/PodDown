"""Integration coverage for production episode API principal scope."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

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


def _principal(*, project_ids: frozenset[UUID] | None = None) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="m03-user",
        tenant_id=UUID(TENANT_ID),
        project_ids=project_ids or frozenset({UUID(PROJECT_ID)}),
        scopes=frozenset({"episodes:read", "episodes:write"}),
        issuer="https://issuer.example.test",
    )


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        (_headers(tenant_id=OTHER_TENANT_ID), 403, "tenant_scope_mismatch"),
        (_headers(project_id=OTHER_PROJECT_ID), 403, "project_scope_forbidden"),
    ],
)
def test_production_episode_creation_rejects_forged_header_scope(
    headers: dict[str, str], status: int, code: str
) -> None:
    """Accepting either unverified header would permit cross-scope creation."""
    with TestClient(
        create_app(auth_mode="oidc", principal_verifier=lambda _request: _principal())
    ) as client:
        response = client.post(
            "/v1/episodes", headers=headers, json={"source": SOURCE, "profile": PROFILE}
        )

    assert response.status_code == status
    assert response.json()["code"] == code


def test_production_episode_creation_requires_injected_principal() -> None:
    """Header-only production requests must not establish an authenticated scope."""
    with TestClient(create_app(auth_mode="oidc")) as client:
        response = client.post(
            "/v1/episodes",
            headers=_headers(),
            json={"source": SOURCE, "profile": PROFILE},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "authenticated_principal_required"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/episodes"),
        ("get", "/v1/episodes/01986e76-4ec6-7b00-8000-000000000004"),
        ("get", "/v1/episodes/01986e76-4ec6-7b00-8000-000000000004/status"),
        ("get", "/v1/episodes/01986e76-4ec6-7b00-8000-000000000004/resources/audio"),
        ("post", "/v1/episodes/01986e76-4ec6-7b00-8000-000000000004/render"),
        ("post", "/v1/episodes/01986e76-4ec6-7b00-8000-000000000004/publish"),
    ],
)
def test_production_protects_every_episode_route_without_a_principal(
    method: str, path: str
) -> None:
    """Skipping scope resolution on any episode route must fail closed."""
    with TestClient(create_app(auth_mode="oidc")) as client:
        if method == "post":
            response = client.post(
                path,
                headers=_headers(),
                json={"source": SOURCE, "profile": PROFILE}
                if path == "/v1/episodes"
                else None,
            )
        else:
            response = client.get(path, headers=_headers())

    assert response.status_code == 401
    assert response.json()["code"] == "authenticated_principal_required"


def test_local_episode_creation_retains_header_scope_compatibility() -> None:
    """Local mode remains the only header-compatible episode API composition."""
    with TestClient(create_app(auth_mode="local")) as client:
        response = client.post(
            "/v1/episodes",
            headers=_headers(),
            json={"source": SOURCE, "profile": PROFILE},
        )

    assert response.status_code == 202
    assert response.json()["episode"]["tenant_id"] == TENANT_ID
