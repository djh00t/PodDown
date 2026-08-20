"""Unit coverage for verified-principal API scope mapping."""

from __future__ import annotations

from uuid import UUID

import pytest

from poddown.api.scope import ScopeError, resolve_request_context
from poddown.auth import AuthenticatedPrincipal

TENANT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10"
PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12"
OTHER_PROJECT_ID = "018f3c7d-9d04-7c25-8e20-9e8e0c4d3b13"


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="m03-user",
        tenant_id=UUID(TENANT_ID),
        project_ids=frozenset({UUID(PROJECT_ID)}),
        scopes=frozenset({"episodes:read"}),
        issuer="https://issuer.example.test",
    )


def test_production_scope_uses_verified_tenant_and_allowlisted_project() -> None:
    """Trusting the tenant header instead of the principal must break this test."""
    context = resolve_request_context(
        principal=_principal(),
        tenant_header=TENANT_ID,
        project_header=PROJECT_ID,
        idempotency_header="m03-unit-001",
    )

    assert context.tenant_id == UUID(TENANT_ID)
    assert context.project_id == UUID(PROJECT_ID)


@pytest.mark.parametrize(
    ("tenant_header", "project_header", "expected_code"),
    [
        (None, PROJECT_ID, "tenant_scope_mismatch"),
        ("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b11", PROJECT_ID, "tenant_scope_mismatch"),
        (TENANT_ID, OTHER_PROJECT_ID, "project_scope_forbidden"),
    ],
)
def test_production_scope_rejects_unverified_header_scope(
    tenant_header: str | None,
    project_header: str,
    expected_code: str,
) -> None:
    """Removing tenant matching or project allowlisting must fail closed."""
    with pytest.raises(ScopeError) as captured:
        resolve_request_context(
            principal=_principal(),
            tenant_header=tenant_header,
            project_header=project_header,
            idempotency_header="m03-unit-002",
        )

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("project_header", "idempotency_header", "expected_code"),
    [
        ("not-a-uuid", "m03-unit-003", "project_scope_forbidden"),
        (PROJECT_ID, "", "invalid_idempotency_key"),
        (PROJECT_ID, "x" * 256, "invalid_idempotency_key"),
    ],
)
def test_production_scope_rejects_malformed_request_context(
    project_header: str,
    idempotency_header: str,
    expected_code: str,
) -> None:
    """Malformed project or replay headers must fail before request dispatch."""
    with pytest.raises(ScopeError) as captured:
        resolve_request_context(
            principal=_principal(),
            tenant_header=TENANT_ID,
            project_header=project_header,
            idempotency_header=idempotency_header,
        )

    assert captured.value.code == expected_code
