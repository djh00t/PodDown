"""BDD bindings for the verified authentication boundary."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthSettings,
    LocalHeaderPrincipalResolver,
    OIDCPrincipalVerifier,
)

scenarios("../features/authentication.feature")


TENANT = UUID("018f2c8b-7b46-7cc5-b2e1-111111111111")
PROJECT = UUID("018f2c8b-7b46-7cc5-b2e1-222222222222")


def _claims(*, subject: str = "user-1") -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "iss": "https://issuer.example.test/",
        "sub": subject,
        "aud": "poddown-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "tenant_id": str(TENANT),
        "project_ids": [str(PROJECT)],
        "scope": "episodes:read episodes:render",
    }


@given("an API authentication verifier with a generated signing key")
def api_verifier(context) -> None:
    key = secrets.token_bytes(32)
    settings = AuthSettings(
        mode="api",
        issuer="https://issuer.example.test/",
        audience="poddown-api",
        verification_key=key,
    )
    context.values["key"] = key
    context.values["verifier"] = OIDCPrincipalVerifier(settings)


@when("I verify a token for a tenant and project")
def verify_valid_token(context) -> None:
    token = jwt.encode(_claims(), context.values["key"], algorithm="HS256")
    context.values["principal"] = context.values["verifier"].verify(f"Bearer {token}")


@then("the principal contains the verified tenant, project and scopes")
def principal_is_scoped(context) -> None:
    principal = context.values["principal"]
    assert isinstance(principal, AuthenticatedPrincipal)
    assert principal.tenant_id == TENANT
    assert PROJECT in principal.project_ids
    assert principal.scopes == frozenset({"episodes:read", "episodes:render"})


@when("I verify a token signed by another generated key")
def verify_invalid_token(context) -> None:
    token = jwt.encode(_claims(), secrets.token_bytes(32), algorithm="HS256")
    with pytest.raises(AuthenticationError):
        context.values["verifier"].verify(f"Bearer {token}")
    context.values["principal"] = None


@then("authentication is rejected without a principal")
def invalid_principal(context) -> None:
    assert context.values["principal"] is None


@given("a local authentication verifier")
def local_verifier(context) -> None:
    context.values["verifier"] = LocalHeaderPrincipalResolver(
        AuthSettings(mode="local")
    )


@when("I resolve a tenant and project from compatibility headers")
def resolve_local_headers(context) -> None:
    context.values["principal"] = context.values["verifier"].resolve(
        tenant_header=str(TENANT), project_header=str(PROJECT)
    )


@then("the local principal is scoped to those identifiers")
def local_principal_is_scoped(context) -> None:
    principal = context.values["principal"]
    assert principal.issuer == "local"
    assert principal.subject == "local-header"
    assert principal.tenant_id == TENANT
    assert principal.project_ids == frozenset({PROJECT})
