"""Verified-principal scope mapping for protected API routes."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError

from poddown.api.models import RequestContext
from poddown.auth import AuthenticatedPrincipal


class ScopeError(Exception):
    """Stable scope-resolution failure for HTTP-boundary mapping."""

    def __init__(self, *, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def resolve_request_context(
    *,
    principal: AuthenticatedPrincipal | None,
    tenant_header: str | None,
    project_header: str | None,
    idempotency_header: str | None,
) -> RequestContext:
    """Map a verified principal and constrained headers to request context."""
    if principal is None:
        raise ScopeError(
            code="authenticated_principal_required",
            message="authenticated principal is required",
            status=401,
        )
    if tenant_header != str(principal.tenant_id):
        raise ScopeError(
            code="tenant_scope_mismatch",
            message="tenant header does not match authenticated principal",
            status=403,
        )
    try:
        project_id = UUID(project_header or "")
    except ValueError as error:
        raise ScopeError(
            code="project_scope_forbidden",
            message="project is not authorized for authenticated principal",
            status=403,
        ) from error
    if project_id.version != 7 or project_id not in principal.project_ids:
        raise ScopeError(
            code="project_scope_forbidden",
            message="project is not authorized for authenticated principal",
            status=403,
        )
    if not isinstance(idempotency_header, str) or not idempotency_header.strip():
        raise ScopeError(
            code="invalid_idempotency_key",
            message="request context is invalid",
            status=400,
        )
    try:
        return RequestContext(
            tenant_id=principal.tenant_id,
            project_id=project_id,
            idempotency_key=idempotency_header,
        )
    except ValidationError as error:
        raise ScopeError(
            code="invalid_idempotency_key",
            message="request context is invalid",
            status=400,
        ) from error


__all__ = ["ScopeError", "resolve_request_context"]
